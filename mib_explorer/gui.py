"""Tkinter interface for browsing locally compiled MIB definitions."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .model import OidDefinition, OidNode, build_oid_tree, parse_oid
from .parser import ParseResult, load_mibs
from .snmp import (
    SnmpValue,
    parse_target_settings,
    table_cell_for_value,
    walk_subtree,
)


def _single_child_chain(node: OidNode) -> tuple[OidNode, ...]:
    chain = [node]
    while len(chain[-1].children) == 1:
        chain.append(next(iter(chain[-1].children.values())))
    return tuple(chain)


@dataclass(frozen=True, slots=True)
class _SnmpResultsBatch:
    request_id: int
    values: tuple[SnmpValue, ...]


@dataclass(frozen=True, slots=True)
class _SnmpResultsComplete:
    request_id: int
    count: int
    aborted: bool = False


@dataclass(frozen=True, slots=True)
class _SnmpResultsFailure:
    request_id: int
    error: str


_SnmpResultsEvent = _SnmpResultsBatch | _SnmpResultsComplete | _SnmpResultsFailure


@dataclass(slots=True)
class _SnmpResultsView:
    tab: ttk.Frame
    tree: ttk.Treeview
    status_var: tk.StringVar
    host: str
    port: int
    community: str
    oid: tuple[int, ...]
    table_columns: tuple[OidDefinition, ...] | None = None
    walk_entire_mib: bool = False
    update_button: ttk.Button | None = None
    abort_button: ttk.Button | None = None
    cancel_event: threading.Event | None = None
    loading: bool = False
    started: bool = False
    received_count: int = 0
    table_row_iids: dict[tuple[int, ...], str] = field(default_factory=dict)
    table_row_cells: dict[tuple[int, ...], dict[tuple[int, ...], str]] = field(
        default_factory=dict
    )
    oid_by_row_iid: dict[str, tuple[int, ...]] = field(default_factory=dict)


_DARK_THEME = "dark"
_SEARCH_MATCH_TAG = "search_match"

_DARK_COLORS = {
    "background": "#252A32",
    "surface": "#323943",
    "field": "#1E232A",
    "foreground": "#E6EAF0",
    "muted": "#A0AAB7",
    "border": "#4A5565",
    "active": "#414B59",
    "selection": "#375A7F",
    "accent": "#477CA8",
    "tab_selected": "#465668",
    "tab_hover": "#3D4856",
}


def _register_dark_theme(style: ttk.Style) -> None:
    if _DARK_THEME in style.theme_names():
        return

    colors = _DARK_COLORS
    style.theme_create(
        _DARK_THEME,
        parent="clam",
        settings={
            ".": {
                "configure": {
                    "background": colors["background"],
                    "foreground": colors["foreground"],
                    "fieldbackground": colors["field"],
                    "bordercolor": colors["border"],
                    "darkcolor": colors["background"],
                    "lightcolor": colors["surface"],
                    "troughcolor": colors["background"],
                    "selectbackground": colors["selection"],
                    "selectforeground": colors["foreground"],
                }
            },
            "TFrame": {"configure": {"background": colors["background"]}},
            "TLabel": {
                "configure": {
                    "background": colors["background"],
                    "foreground": colors["foreground"],
                }
            },
            "TButton": {
                "configure": {
                    "background": colors["surface"],
                    "foreground": colors["foreground"],
                    "bordercolor": colors["border"],
                    "focuscolor": colors["accent"],
                    "padding": (8, 4),
                },
                "map": {
                    "background": [
                        ("pressed", colors["accent"]),
                        ("active", colors["active"]),
                        ("disabled", colors["surface"]),
                    ],
                    "foreground": [("disabled", colors["muted"])],
                },
            },
            "TMenubutton": {
                "configure": {
                    "background": colors["surface"],
                    "foreground": colors["foreground"],
                    "bordercolor": colors["border"],
                    "focuscolor": colors["accent"],
                    "padding": (8, 4),
                },
                "map": {
                    "background": [
                        ("pressed", colors["accent"]),
                        ("active", colors["active"]),
                    ]
                },
            },
            "TNotebook": {
                "configure": {
                    "background": colors["background"],
                    "bordercolor": colors["border"],
                    "tabmargins": (4, 4, 0, 0),
                }
            },
            "TNotebook.Tab": {
                "configure": {
                    "background": colors["surface"],
                    "foreground": colors["muted"],
                    "bordercolor": colors["border"],
                    "padding": (10, 1),
                },
                "map": {
                    "background": [
                        ("selected", colors["tab_selected"]),
                        ("active", colors["tab_hover"]),
                    ],
                    "foreground": [
                        ("selected", colors["foreground"]),
                        ("active", colors["foreground"]),
                    ],
                },
            },
            "TEntry": {
                "configure": {
                    "fieldbackground": colors["field"],
                    "foreground": colors["foreground"],
                    "insertcolor": colors["foreground"],
                    "bordercolor": colors["border"],
                    "lightcolor": colors["border"],
                    "darkcolor": colors["border"],
                    "selectbackground": colors["selection"],
                    "selectforeground": colors["foreground"],
                },
                "map": {
                    "fieldbackground": [("disabled", colors["surface"])],
                    "foreground": [("disabled", colors["muted"])],
                },
            },
            "Treeview": {
                "configure": {
                    "background": colors["field"],
                    "fieldbackground": colors["field"],
                    "foreground": colors["foreground"],
                    "bordercolor": colors["border"],
                },
                "map": {
                    "background": [("selected", colors["selection"])],
                    "foreground": [("selected", colors["foreground"])],
                },
            },
            "Treeview.Heading": {
                "configure": {
                    "background": colors["surface"],
                    "foreground": colors["foreground"],
                    "bordercolor": colors["border"],
                },
                "map": {"background": [("active", colors["active"])]},
            },
            "TLabelframe": {
                "configure": {
                    "background": colors["background"],
                    "foreground": colors["foreground"],
                    "bordercolor": colors["border"],
                }
            },
            "TLabelframe.Label": {
                "configure": {
                    "background": colors["background"],
                    "foreground": colors["foreground"],
                }
            },
            "TScrollbar": {
                "configure": {
                    "background": colors["surface"],
                    "troughcolor": colors["background"],
                    "bordercolor": colors["border"],
                    "arrowcolor": colors["foreground"],
                },
                "map": {
                    "background": [("active", colors["active"]), ("pressed", colors["accent"])],
                },
            },
            "TPanedwindow": {"configure": {"background": colors["border"]}},
            "TSeparator": {"configure": {"background": colors["border"]}},
        },
    )


class MibExplorerApp(tk.Tk):
    def __init__(self, startup_paths: Sequence[str | Path] = ()) -> None:
        super().__init__()
        self.title("MIB Explorer")
        self.geometry("1120x720")
        self.minsize(760, 480)
        self._style = ttk.Style(self)
        _register_dark_theme(self._style)
        self._default_window_background = self.cget("background")

        self._definitions: dict[tuple[str, str, tuple[int, ...]], OidDefinition] = {}
        self._warnings: list[str] = []
        self._nodes_by_oid: dict[tuple[int, ...], OidNode] = {}
        self._iid_by_oid: dict[tuple[int, ...], str] = {}
        self._oid_by_iid: dict[str, tuple[int, ...]] = {}
        self._definitions_by_iid: dict[str, tuple[OidDefinition, ...]] = {}
        self._search_matches: list[tuple[int, ...]] = []
        self._search_pattern: re.Pattern[str] | None = None
        self._search_highlight_iids: set[str] = set()
        self._search_index = 0
        self._prev_query: str = ''
        self._loading = False
        self._source_link_tags: list[str] = []
        self._results: queue.Queue[tuple[ParseResult | None, Exception | None]] = queue.Queue()
        self._snmp_results: queue.Queue[_SnmpResultsEvent] = queue.Queue()
        self._snmp_views: dict[int, _SnmpResultsView] = {}
        self._next_snmp_request_id = 0

        self._build_widgets()
        self._set_theme("dark")
        self.after(100, self._poll_results)
        if startup_paths:
            self._start_loading(startup_paths)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.grid(row=0, column=0, sticky="ew")

        self._files_button = ttk.Button(toolbar, text="Open MIB/SMI files…", command=self._choose_files)
        self._files_button.grid(row=0, column=0, padx=(0, 6))
        self._folder_button = ttk.Button(toolbar, text="Open folder…", command=self._choose_folder)
        self._folder_button.grid(row=0, column=1, padx=(0, 12))

        ttk.Label(toolbar, text="Search:").grid(row=0, column=2, padx=(0, 5))
        self._search_var = tk.StringVar()
        self._search_entry = ttk.Entry(toolbar, textvariable=self._search_var)
        self._search_entry.grid(row=0, column=3, sticky="ew")
        self._search_entry.bind("<Return>", self._find_matches)
        self._search_entry.bind("<Shift-Return>", self._previous_match)
        ttk.Button(toolbar, text="Expand all", command=self._expand_all).grid(
            row=0, column=4, padx=(12, 0)
        )
        ttk.Button(toolbar, text="Collapse all", command=self._collapse_all).grid(
            row=0, column=5, padx=(6, 0)
        )
        self._theme_var = tk.StringVar(value=self._style.theme_use())
        self._theme_menu = tk.Menu(toolbar, tearoff=False)
        self._default_theme_menu_colors = {
            option: self._theme_menu.cget(option)
            for option in (
                "background",
                "foreground",
                "activebackground",
                "activeforeground",
                "disabledforeground",
                "selectcolor",
            )
        }
        for theme in self._style.theme_names():
            self._theme_menu.add_radiobutton(
                label=theme,
                variable=self._theme_var,
                value=theme,
                command=lambda selected_theme=theme: self._set_theme(selected_theme),
            )
        self._theme_button = ttk.Menubutton(
            toolbar,
            text=f"Theme: {self._theme_var.get()}",
            menu=self._theme_menu,
        )
        self._theme_button.grid(row=0, column=6, padx=(10, 0))

        snmp_frame = ttk.LabelFrame(self, text="SNMP target", padding=(8, 5))
        snmp_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        snmp_frame.columnconfigure(1, weight=2)
        snmp_frame.columnconfigure(5, weight=1)
        self._snmp_host_var = tk.StringVar()
        self._snmp_port_var = tk.StringVar(value="161")
        self._snmp_community_var = tk.StringVar(value="public")
        ttk.Label(snmp_frame, text="Agent:").grid(row=0, column=0, padx=(0, 5))
        ttk.Entry(snmp_frame, textvariable=self._snmp_host_var, width=28).grid(
            row=0, column=1, sticky="ew", padx=(0, 12)
        )
        ttk.Label(snmp_frame, text="Port:").grid(row=0, column=2, padx=(0, 5))
        ttk.Entry(snmp_frame, textvariable=self._snmp_port_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(0, 12)
        )
        ttk.Label(snmp_frame, text="Community (v2c):").grid(row=0, column=4, padx=(0, 5))
        ttk.Entry(
            snmp_frame,
            textvariable=self._snmp_community_var,
            width=24,
        ).grid(row=0, column=5, sticky="ew")

        self._notebook = ttk.Notebook(self)
        self._notebook.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        self._browser_tab = ttk.Frame(self._notebook)
        browser_tab = self._browser_tab
        browser_tab.rowconfigure(0, weight=1)
        browser_tab.columnconfigure(0, weight=1)
        self._notebook.add(browser_tab, text="MIB Browser")

        content = ttk.Panedwindow(browser_tab, orient="horizontal")
        content.grid(row=0, column=0, sticky="nsew")

        tree_frame = ttk.Frame(content, padding=(0, 0, 5, 0))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self._style.configure("MibExplorer.Treeview", font="TkFixedFont")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("oid", "kind", "syntax"),
            selectmode="browse",
            style="MibExplorer.Treeview",
        )
        self._tree.heading("#0", text="Name")
        self._tree.heading("oid", text="Numeric OID")
        self._tree.heading("kind", text="Type")
        self._tree.heading("syntax", text="Syntax")
        self._tree.column("#0", width=350, minwidth=300, stretch=True)
        self._tree.column("oid", width=180, minwidth=110, stretch=True)
        self._tree.column("kind", width=100, minwidth=70, stretch=True)
        self._tree.column("syntax", width=100, minwidth=70, stretch=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self._tree.bind("<<TreeviewSelect>>", self._show_selected_details)
        self._tree.bind("<Return>", self._find_matches)
        self._tree.bind("<n>", self._find_matches)
        self._tree.bind("<Shift-Return>", self._previous_match)
        self._tree.bind("<Shift-N>", self._previous_match)
        self._tree_context_menu = tk.Menu(self._tree, tearoff=False)
        self._tree.bind("<Button-3>", self._show_tree_context_menu)
        content.add(tree_frame, weight=3)

        details_frame = ttk.LabelFrame(content, text="Definition details", padding=6)
        details_frame.rowconfigure(0, weight=1)
        details_frame.columnconfigure(0, weight=1)
        self._details = tk.Text(details_frame, wrap="word", state="disabled", padx=7, pady=6, width=10)
        details_scroll = ttk.Scrollbar(details_frame, orient="vertical", command=self._details.yview)
        self._details.configure(yscrollcommand=details_scroll.set)
        self._details.grid(row=0, column=0, sticky="nsew")
        details_scroll.grid(row=0, column=1, sticky="ns")
        content.add(details_frame, weight=2)

        self._status_var = tk.StringVar(value="Open MIB/SMI files or a folder to get started.")
        ttk.Label(self, textvariable=self._status_var, anchor="w", padding=(8, 3)).grid(
            row=3, column=0, sticky="ew"
        )

        warning_frame = ttk.LabelFrame(self, text="Parser warnings", padding=(6, 3))
        warning_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))
        warning_frame.columnconfigure(0, weight=1)
        self._warning_text = tk.Text(warning_frame, height=4, wrap="word", state="disabled")
        warning_scroll = ttk.Scrollbar(warning_frame, orient="vertical", command=self._warning_text.yview)
        self._warning_text.configure(yscrollcommand=warning_scroll.set)
        self._warning_text.grid(row=0, column=0, sticky="ew")
        warning_scroll.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(4, weight=0)
        self._configure_search_match_tag()
        self._set_text(self._details, "Select an OID node to inspect its MIB definitions.")
        self._refresh_warnings()
        text_options = (
            "background",
            "foreground",
            "insertbackground",
            "selectbackground",
            "selectforeground",
            "highlightbackground",
            "highlightcolor",
        )
        self._default_text_colors = [
            (widget, {option: widget.cget(option) for option in text_options})
            for widget in (self._details, self._warning_text)
        ]
        self._bind_search_shortcuts(self)

    def _bind_search_shortcuts(self, widget: tk.Misc) -> None:
        widget.bind("<Control-f>", self._focus_search, add="+")

    def _set_theme(self, theme: str) -> None:
        if theme not in self._style.theme_names():
            return
        self._style.theme_use(theme)
        self._style.configure("MibExplorer.Treeview", font="TkFixedFont")
        self._theme_var.set(theme)
        self._theme_button.configure(text=f"Theme: {theme}")
        if theme == _DARK_THEME:
            self._apply_dark_colors()
        else:
            self.configure(background=self._default_window_background)
            self._theme_menu.configure(**self._default_theme_menu_colors)
            for widget, colors in self._default_text_colors:
                widget.configure(**colors)
        self._configure_search_match_tag()

    def _apply_dark_colors(self) -> None:
        colors = _DARK_COLORS
        self.configure(background=colors["background"])
        self._theme_menu.configure(
            background=colors["surface"],
            foreground=colors["foreground"],
            activebackground=colors["selection"],
            activeforeground=colors["foreground"],
            disabledforeground=colors["muted"],
            selectcolor=colors["accent"],
        )
        for widget in (self._details, self._warning_text):
            widget.configure(
                background=colors["field"],
                foreground=colors["foreground"],
                insertbackground=colors["foreground"],
                selectbackground=colors["selection"],
                selectforeground=colors["foreground"],
                highlightbackground=colors["border"],
                highlightcolor=colors["accent"],
            )

    def _configure_search_match_tag(self) -> None:
        if self._style.theme_use() == _DARK_THEME:
            background, foreground = "#66531F", "#FFF1C2"
        else:
            background, foreground = "#FFF0A8", "#332B14"
        self._tree.tag_configure(
            _SEARCH_MATCH_TAG,
            background=background,
            foreground=foreground,
        )
        self._details.tag_configure(
            _SEARCH_MATCH_TAG,
            background=background,
            foreground=foreground,
        )

    def _choose_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Open MIB/SMI files",
        )
        if paths:
            self._start_loading(paths)

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Open MIB/SMI folder", mustexist=True)
        if path:
            self._start_loading((path,))

    def _show_tree_context_menu(self, event: tk.Event) -> str:
        iid = self._tree.identify_row(event.y)
        if not iid:
            return "break"

        self._tree.selection_set(iid)
        self._tree.focus(iid)
        oid = self._oid_by_iid.get(iid)
        self._tree_context_menu.delete(0, "end")
        self._tree_context_menu.add_command(
            label="Get subtree",
            command=self._get_selected_subtree,
            state="normal" if oid else "disabled",
        )
        self._tree_context_menu.add_command(
            label="Walk",
            command=self._walk_selected_oid,
            state="normal" if oid else "disabled",
        )
        if self._table_definition_for_iid(iid) is not None:
            self._tree_context_menu.add_command(
                label="Get table",
                command=self._get_selected_table,
            )
        self._tree_context_menu.tk_popup(event.x_root, event.y_root)
        return "break"


    @staticmethod
    def _kind_is(definition: OidDefinition, *expected_kinds: str) -> bool:
        kind = "".join(character for character in definition.kind.casefold() if character.isalnum())
        return kind in expected_kinds

    def _table_definition_for_iid(self, iid: str) -> OidDefinition | None:
        return next(
            (
                definition
                for definition in self._definitions_by_iid.get(iid, ())
                if self._kind_is(definition, "table", "mibtable")
            ),
            None,
        )

    def _table_column_definitions(self, table_definition: OidDefinition) -> tuple[OidDefinition, ...]:
        definitions = tuple(self._definitions.values())
        row_oids = [
            definition.oid
            for definition in definitions
            if self._kind_is(definition, "row", "tablerow", "mibtablerow")
            and definition.oid[: len(table_definition.oid)] == table_definition.oid
        ]
        row_oid = min(row_oids, key=len) if row_oids else table_definition.oid
        columns_by_oid: dict[tuple[int, ...], OidDefinition] = {}
        for definition in sorted(definitions, key=lambda item: (item.oid, item.module, item.name)):
            if (
                self._kind_is(definition, "column", "tablecolumn", "mibtablecolumn")
                and definition.oid[: len(row_oid)] == row_oid
            ):
                columns_by_oid.setdefault(definition.oid, definition)
        return tuple(columns_by_oid.values())

    def _get_selected_subtree(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        oid = self._oid_by_iid.get(selection[0])
        if not oid:
            return

        try:
            host, port, community = parse_target_settings(
                self._snmp_host_var.get(),
                self._snmp_port_var.get(),
                self._snmp_community_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Invalid SNMP target", str(exc), parent=self)
            return

        self._open_snmp_results_tab(host, port, community, oid)

    def _walk_selected_oid(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        oid = self._oid_by_iid.get(selection[0])
        if not oid:
            return

        try:
            host, port, community = parse_target_settings(
                self._snmp_host_var.get(),
                self._snmp_port_var.get(),
                self._snmp_community_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Invalid SNMP target", str(exc), parent=self)
            return

        self._open_snmp_results_tab(
            host,
            port,
            community,
            oid,
            walk_entire_mib=True,
        )

    def _get_selected_table(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        table_definition = self._table_definition_for_iid(selection[0])
        if table_definition is None:
            return

        try:
            host, port, community = parse_target_settings(
                self._snmp_host_var.get(),
                self._snmp_port_var.get(),
                self._snmp_community_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Invalid SNMP target", str(exc), parent=self)
            return

        self._open_snmp_results_tab(
            host,
            port,
            community,
            table_definition.oid,
            table_columns=self._table_column_definitions(table_definition),
        )

    def _open_snmp_results_tab(
        self,
        host: str,
        port: int,
        community: str,
        oid: tuple[int, ...],
        table_columns: tuple[OidDefinition, ...] | None = None,
        walk_entire_mib: bool = False,
    ) -> None:
        oid_text = "." + ".".join(str(arc) for arc in oid)
        result_tab = ttk.Frame(self._notebook, padding=8)
        result_tab.rowconfigure(0, weight=1)
        result_tab.columnconfigure(0, weight=1)
        tab_label = f"{host} - {self._oid_name_for_text(oid_text)}"
        self._notebook.add(result_tab, text=tab_label)
        self._notebook.select(result_tab)

        table_frame = ttk.Frame(result_tab)
        table_frame.grid(row=0, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        if table_columns:
            column_ids = ("index", *(f"column_{index}" for index in range(len(table_columns))))
            tree = ttk.Treeview(
                table_frame,
                columns=column_ids,
                show="headings",
                selectmode="browse",
            )
            tree.heading("index", text="Index")
            tree.column("index", width=140, minwidth=100, stretch=False)
            for column_id, definition in zip(column_ids[1:], table_columns):
                tree.heading(column_id, text=definition.name)
                tree.column(column_id, width=160, minwidth=100, stretch=False)
        else:
            tree = ttk.Treeview(
                table_frame,
                columns=("name", "oid", "syntax", "value"),
                show="headings",
                selectmode="browse",
            )
            tree.heading("name", text="OID name")
            tree.heading("oid", text="Numeric OID")
            tree.heading("syntax", text="Type")
            tree.heading("value", text="Value")
            tree.column("name", width=220, minwidth=140, stretch=False)
            tree.column("oid", width=230, minwidth=150, stretch=False)
            tree.column("syntax", width=130, minwidth=90, stretch=False)
            tree.column("value", width=520, minwidth=200, stretch=True)
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

        if walk_entire_mib:
            request_kind = "walk"
        elif table_columns is not None:
            request_kind = "table"
        else:
            request_kind = "subtree"
        status_var = tk.StringVar(
            value=f"Reading {request_kind} {oid_text} from {host}:{port} with SNMPv2c…"
        )
        footer = ttk.Frame(result_tab)
        footer.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=status_var, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )

        self._next_snmp_request_id += 1
        request_id = self._next_snmp_request_id
        view = _SnmpResultsView(
            result_tab,
            tree,
            status_var,
            host,
            port,
            community,
            oid,
            table_columns,
            walk_entire_mib=walk_entire_mib,
        )
        self._snmp_views[request_id] = view
        view.update_button = ttk.Button(
            footer,
            text="Update values",
            command=lambda: self._start_snmp_request(request_id),
        )
        view.update_button.grid(row=0, column=1, padx=(0, 6), sticky="e")
        view.abort_button = ttk.Button(
            footer,
            text="Abort read",
            command=lambda: self._abort_snmp_request(request_id),
            state="disabled",
        )
        view.abort_button.grid(row=0, column=2, padx=(0, 6), sticky="e")
        ttk.Button(
            footer,
            text="Close tab",
            command=lambda: self._close_snmp_tab(request_id),
        ).grid(row=0, column=3, sticky="e")
        tree.bind(
            "<Double-1>",
            lambda event, selected_request_id=request_id: self._show_result_oid(
                selected_request_id,
                event,
            ),
        )
        self._start_snmp_request(request_id)

    def _start_snmp_request(self, request_id: int) -> None:
        view = self._snmp_views.get(request_id)
        if view is None or view.loading or view.update_button is None:
            return

        if view.walk_entire_mib:
            request_kind = "walk"
        elif view.table_columns is not None:
            request_kind = "table"
        else:
            request_kind = "subtree"
        action = "Updating" if view.started else "Reading"
        self._clear_snmp_result_view(view)
        oid_text = "." + ".".join(str(arc) for arc in view.oid)
        view.status_var.set(
            f"{action} {request_kind} {oid_text} from {view.host}:{view.port} with SNMPv2c…"
        )
        view.started = True
        view.loading = True
        view.cancel_event = threading.Event()
        view.update_button.configure(state="disabled")
        if view.abort_button is not None:
            view.abort_button.configure(state="normal")
        worker = threading.Thread(
            target=self._get_subtree_in_background,
            args=(
                request_id,
                view.host,
                view.port,
                view.community,
                view.oid,
                view.cancel_event,
                view.walk_entire_mib,
            ),
            daemon=True,
        )
        worker.start()

    def _get_subtree_in_background(
        self,
        request_id: int,
        host: str,
        port: int,
        community: str,
        oid: tuple[int, ...],
        cancel_event: threading.Event,
        walk_entire_mib: bool,
    ) -> None:
        try:
            def report_batch(values: tuple[SnmpValue, ...]) -> None:
                self._snmp_results.put(_SnmpResultsBatch(request_id, values))

            result = walk_subtree(
                host,
                port,
                community,
                oid,
                report_batch,
                cancel_event=cancel_event,
                walk_entire_mib=walk_entire_mib,
            )
        except Exception as exc:  # noqa: BLE001
            self._snmp_results.put(_SnmpResultsFailure(request_id, str(exc)))
        else:
            self._snmp_results.put(
                _SnmpResultsComplete(request_id, result.count, result.aborted)
            )

    def _abort_snmp_request(self, request_id: int) -> None:
        view = self._snmp_views.get(request_id)
        if view is None or not view.loading or view.cancel_event is None:
            return

        view.cancel_event.set()
        if view.abort_button is not None:
            view.abort_button.configure(state="disabled")
        view.status_var.set("Aborting SNMP walk; keeping fetched values…")

    def _show_result_oid(self, request_id: int, event: tk.Event) -> str:
        view = self._snmp_views.get(request_id)
        if view is None:
            return "break"

        row_iid = view.tree.identify_row(event.y)
        oid = view.oid_by_row_iid.get(row_iid)
        if oid is not None:
            self._navigate_to_oid(oid)
        return "break"

    def _navigate_to_oid(self, oid: tuple[int, ...]) -> None:
        target_iid = None
        for prefix_length in range(len(oid), 0, -1):
            target_iid = self._iid_by_oid.get(oid[:prefix_length])
            if target_iid is not None:
                break
        if target_iid is None:
            return

        self._notebook.select(self._browser_tab)
        ancestor_iid = self._tree.parent(target_iid)
        while ancestor_iid:
            self._tree.item(ancestor_iid, open=True)
            ancestor_iid = self._tree.parent(ancestor_iid)
        self._tree.selection_set(target_iid)
        self._tree.focus(target_iid)
        self._tree.see(target_iid)
        self._show_selected_details()

    def _oid_name_for_result(self, oid_text: str) -> str:
        name = self._oid_name_for_text(oid_text)
        try:
            oid = parse_oid(oid_text)
        except ValueError:
            return name
        return name if self._result_oid_is_defined(oid) else f"{name}*"

    def _result_oid_is_defined(self, oid: tuple[int, ...]) -> bool:
        exact_node = self._nodes_by_oid.get(oid)
        if exact_node is not None and exact_node.definitions:
            return True

        for prefix_length in range(len(oid) - 1, 0, -1):
            node = self._nodes_by_oid.get(oid[:prefix_length])
            if node is None:
                continue
            suffix = oid[prefix_length:]
            for definition in node.definitions:
                kind = "".join(
                    character
                    for character in definition.kind.casefold()
                    if character.isalnum()
                )
                if kind in {"scalar", "mibscalar"} and suffix == (0,):
                    return True
                if kind in {"column", "tablecolumn", "mibtablecolumn"} and suffix:
                    return True
        return False

    def _format_result_value(self, value: SnmpValue) -> str:
        if value.syntax.casefold() not in {"integer", "integer32"}:
            return value.value
        try:
            numeric_value = int(value.value)
            oid = parse_oid(value.oid)
        except ValueError:
            return value.value

        for prefix_length in range(len(oid), 0, -1):
            node = self._nodes_by_oid.get(oid[:prefix_length])
            if node is None:
                continue
            enum_definitions = [
                definition
                for definition in node.definitions
                if definition.integer_enumerations
            ]
            if not enum_definitions:
                continue

            labels = {
                name
                for definition in enum_definitions
                for name, number in definition.integer_enumerations
                if number == numeric_value
            }
            if len(labels) == 1:
                label = next(iter(labels))
                return f"{label}({numeric_value})"
            return value.value

        return value.value

    def _oid_name_for_text(self, oid_text: str) -> str:
        try:
            oid = parse_oid(oid_text)
        except ValueError:
            return oid_text

        for prefix_length in range(len(oid), 0, -1):
            node = self._nodes_by_oid.get(oid[:prefix_length])
            if node is None or not node.definitions:
                continue
            names = sorted({definition.name for definition in node.definitions})
            suffix = "".join(f".{arc}" for arc in oid[prefix_length:])
            return ", ".join(f"{name}{suffix}" for name in names)
        return oid_text

    @staticmethod
    def _clear_snmp_result_view(view: _SnmpResultsView) -> None:
        existing_rows = view.tree.get_children("")
        if existing_rows:
            view.tree.delete(*existing_rows)
        view.received_count = 0
        view.table_row_iids.clear()
        view.table_row_cells.clear()
        view.oid_by_row_iid.clear()

    def _poll_snmp_results(self) -> None:
        # Limit each pass so Tk can redraw between batches during large walks.
        for _ in range(20):
            try:
                event = self._snmp_results.get_nowait()
            except queue.Empty:
                return

            view = self._snmp_views.get(event.request_id)
            if view is None:
                continue
            if isinstance(event, _SnmpResultsBatch):
                self._append_snmp_values(view, event.values)
            elif isinstance(event, _SnmpResultsComplete):
                view.loading = False
                view.cancel_event = None
                if view.update_button is not None:
                    view.update_button.configure(state="normal")
                if view.abort_button is not None:
                    view.abort_button.configure(state="disabled")
                self._finish_snmp_results(view, event.count, event.aborted)
            else:
                view.loading = False
                view.cancel_event = None
                if view.update_button is not None:
                    view.update_button.configure(state="normal")
                if view.abort_button is not None:
                    view.abort_button.configure(state="disabled")
                view.status_var.set(f"SNMP walk failed: {event.error}")

    def _append_snmp_values(
        self,
        view: _SnmpResultsView,
        values: tuple[SnmpValue, ...],
    ) -> None:
        view.received_count += len(values)
        if view.table_columns:
            for value in values:
                cell = table_cell_for_value(value, view.table_columns)
                if cell is None:
                    continue
                row_index, column_oid, _cell_value = cell
                row_iid = view.table_row_iids.get(row_index)
                if row_iid is None:
                    row_iid = view.tree.insert(
                        "",
                        "end",
                        values=(
                            ".".join(str(arc) for arc in row_index),
                            *("" for _ in view.table_columns),
                        ),
                    )
                    view.table_row_iids[row_index] = row_iid
                    view.table_row_cells[row_index] = {}

                view.oid_by_row_iid.setdefault(row_iid, parse_oid(value.oid))
                cells = view.table_row_cells[row_index]
                cells[column_oid] = self._format_result_value(value)
                row_values = (
                    ".".join(str(arc) for arc in row_index),
                    *(cells.get(column.oid, "") for column in view.table_columns),
                )
                view.tree.item(row_iid, values=row_values)
        else:
            for value in values:
                row_iid = view.tree.insert(
                    "",
                    "end",
                    values=(
                        self._oid_name_for_result(value.oid),
                        value.oid,
                        value.syntax,
                        self._format_result_value(value),
                    ),
                )
                try:
                    view.oid_by_row_iid[row_iid] = parse_oid(value.oid)
                except ValueError:
                    continue

        if view.table_columns:
            view.status_var.set(
                f"Received {view.received_count} value(s); "
                f"{len(view.table_row_iids)} table row(s) so far…"
            )
        elif view.table_columns is not None:
            view.status_var.set(
                f"Received {view.received_count} value(s); no table-column definitions "
                "were loaded, so raw OIDs are shown…"
            )
        else:
            view.status_var.set(f"Received {view.received_count} value(s)…")

    @staticmethod
    def _finish_snmp_results(
        view: _SnmpResultsView,
        count: int,
        aborted: bool = False,
    ) -> None:
        if aborted:
            if count == 0:
                view.status_var.set("Read aborted; no values were received.")
            elif view.table_columns:
                view.status_var.set(
                    f"Read aborted after {count} value(s) across "
                    f"{len(view.table_row_iids)} table row(s)."
                )
            elif view.table_columns is not None:
                view.status_var.set(
                    f"Read aborted after {count} value(s); raw OIDs are shown because "
                    "no table-column definitions were loaded."
                )
            else:
                view.status_var.set(f"Read aborted after {count} value(s).")
            return

        if count == 0:
            view.status_var.set("No descendant OIDs were returned by the agent.")
        elif view.table_columns:
            if view.table_row_iids:
                view.status_var.set(
                    f"Received {count} value(s) across "
                    f"{len(view.table_row_iids)} table row(s)."
                )
            else:
                view.status_var.set("No table rows matched the loaded column definitions.")
        elif view.table_columns is not None:
            view.status_var.set(
                f"Received {count} value(s); no table-column definitions were loaded, "
                "so raw OIDs are shown."
            )
        else:
            view.status_var.set(f"Received {count} value(s).")

    def _close_snmp_tab(self, request_id: int) -> None:
        view = self._snmp_views.pop(request_id, None)
        if view is not None:
            if view.cancel_event is not None:
                view.cancel_event.set()
            self._notebook.forget(view.tab)
            view.tab.destroy()

    def _start_loading(self, paths: Sequence[str | Path]) -> None:
        if self._loading:
            return
        self._loading = True
        self._set_loading_controls(True)
        self._status_var.set("Parsing MIB files…")
        worker = threading.Thread(target=self._load_in_background, args=(tuple(paths),), daemon=True)
        worker.start()

    def _load_in_background(self, paths: Sequence[str | Path]) -> None:
        try:
            self._results.put((load_mibs(paths), None))
        # Forward worker failures to Tk's main thread so the UI can report them.
        except Exception as exc:  # noqa: BLE001
            self._results.put((None, exc))

    def _poll_results(self) -> None:
        self._poll_snmp_results()
        try:
            result, error = self._results.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_results)
            return

        self._loading = False
        self._set_loading_controls(False)
        if error is not None:
            self._status_var.set("MIB loading failed.")
            messagebox.showerror("MIB loading failed", str(error), parent=self)
        elif result is not None:
            self._accept_result(result)
        self.after(100, self._poll_results)

    def _set_loading_controls(self, loading: bool) -> None:
        state = "disabled" if loading else "normal"
        self._files_button.configure(state=state)
        self._folder_button.configure(state=state)

    def _accept_result(self, result: ParseResult) -> None:
        for definition in result.definitions:
            key = (definition.module, definition.name, definition.oid)
            self._definitions[key] = definition
        self._warnings.extend(result.warnings)
        self._refresh_warnings()
        self._rebuild_tree()

        if result.modules:
            self._status_var.set(
                f"Compiled {len(result.modules)} module(s); "
                f"showing {len(self._definitions)} definition(s)."
            )
        else:
            self._status_var.set("No modules were compiled. See parser warnings below.")

    def _rebuild_tree(self) -> None:
        self._clear_search_highlights()
        self._tree.delete(*self._tree.get_children(""))
        self._nodes_by_oid.clear()
        self._iid_by_oid.clear()
        self._oid_by_iid.clear()
        self._definitions_by_iid.clear()
        root = build_oid_tree(self._definitions.values())

        root_iid = self._tree.insert(
            "",
            "end",
            text=root.label,
            values=(root.oid_text, root.kind, root.syntax),
            open=True,
        )
        self._nodes_by_oid[root.oid] = root
        self._iid_by_oid[root.oid] = root_iid
        self._oid_by_iid[root_iid] = root.oid
        self._definitions_by_iid[root_iid] = ()

        def insert(parent_iid: str, node: OidNode) -> None:
            chain = _single_child_chain(node)
            endpoint = chain[-1]
            definitions = tuple(
                definition for chain_node in chain for definition in chain_node.definitions
            )
            kinds = sorted({definition.kind for definition in definitions if definition.kind})
            syntaxes = sorted({definition.syntax for definition in definitions if definition.syntax})
            iid = self._tree.insert(
                parent_iid,
                "end",
                text=".".join(chain_node.label for chain_node in chain),
                values=(
                    endpoint.oid_text,
                    ", ".join(kinds),
                    ", ".join(syntaxes),
                ),
                open=False,
            )
            self._oid_by_iid[iid] = endpoint.oid
            self._definitions_by_iid[iid] = definitions
            for chain_node in chain:
                self._nodes_by_oid[chain_node.oid] = chain_node
                self._iid_by_oid[chain_node.oid] = iid
            for arc in sorted(endpoint.children):
                insert(iid, endpoint.children[arc])

        for arc in sorted(root.children):
            insert(root_iid, root.children[arc])

        if self._search_var.get().strip():
            self._find_matches()

    def _expand_all(self) -> None:
        self._set_descendants_open("", True)

    def _collapse_all(self) -> None:
        root_iid = self._iid_by_oid.get(())
        if root_iid is None:
            return
        self._set_descendants_open(root_iid, False)
        self._tree.item(root_iid, open=True)

    def _set_descendants_open(self, parent_iid: str, is_open: bool) -> None:
        for iid in self._tree.get_children(parent_iid):
            self._tree.item(iid, open=is_open)
            self._set_descendants_open(iid, is_open)

    def _show_selected_details(self, _event: tk.Event | None = None) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        oid = self._oid_by_iid.get(selection[0])
        if oid is None:
            return
        node = self._nodes_by_oid[oid]
        definitions = self._definitions_by_iid.get(selection[0], tuple(node.definitions))
        for tag in self._source_link_tags:
            self._details.tag_delete(tag)
        self._source_link_tags.clear()
        self._clear_details_search_highlight()

        if not definitions:
            self._set_text(
                self._details,
                f"Structural OID node\n\nNumeric OID: {node.oid_text}\n"
                "No loaded MIB defines this exact node.",
            )
            self._highlight_details_matches()
            return

        separator = "\n\n" + "─" * 48 + "\n\n"
        parts = ["\n\n"]
        links: list[tuple[int, int, str]] = []
        offset = len(parts[0])

        for index, definition in enumerate(definitions):
            if index:
                parts.append(separator)
                offset += len(separator)

            lines = [definition.name, f"Module: {definition.module}"]
            filename = definition.source_file or "Unknown"
            filename_offset = offset + sum(len(line) + 1 for line in lines) + len("File: ")
            lines.extend((f"File: {filename}", f"Numeric OID: {definition.oid_text}"))
            if definition.kind:
                lines.append(f"Type: {definition.kind}")
            if definition.syntax:
                lines.append(f"Syntax: {definition.syntax}")
            if definition.access:
                lines.append(f"Access: {definition.access}")
            if definition.status:
                lines.append(f"Status: {definition.status}")
            if definition.description:
                lines.extend(("", "Description:", definition.description.strip()))

            block = "\n".join(lines)
            if definition.source_path:
                links.append((filename_offset, filename_offset + len(filename), definition.source_path))
            parts.append(block)
            offset += len(block)

        self._set_text(self._details, "".join(parts))
        for index, (start, end, source_path) in enumerate(links):
            tag = f"source-file-link-{index}"
            self._source_link_tags.append(tag)
            start_index = self._details.index(f"1.0 + {start} chars")
            end_index = self._details.index(f"1.0 + {end} chars")
            self._details.tag_add(tag, start_index, end_index)
            self._details.tag_configure(tag, underline=True)
            self._details.tag_bind(
                tag,
                "<Button-1>",
                lambda _event, path=source_path: self._open_source_file(path),
            )
            self._details.tag_bind(
                tag,
                "<Enter>",
                lambda _event: self._details.configure(cursor="hand2"),
            )
            self._details.tag_bind(
                tag,
                "<Leave>",
                lambda _event: self._details.configure(cursor=""),
            )
        self._highlight_details_matches()

    def _clear_details_search_highlight(self) -> None:
        self._details.tag_remove(_SEARCH_MATCH_TAG, "1.0", "end")

    def _highlight_details_matches(self) -> None:
        self._clear_details_search_highlight()
        if self._search_pattern is None:
            return

        details = self._details.get("1.0", "end-1c")
        for match in self._search_pattern.finditer(details):
            start = self._text_index_for_offset(details, match.start())
            end = self._text_index_for_offset(details, match.end())
            self._details.tag_add(_SEARCH_MATCH_TAG, start, end)

    @staticmethod
    def _text_index_for_offset(text: str, offset: int) -> str:
        line = text.count("\n", 0, offset) + 1
        line_start = text.rfind("\n", 0, offset) + 1
        return f"{line}.{offset - line_start}"

    def _clear_search_highlights(self) -> None:
        for iid in self._search_highlight_iids:
            tags = self._tree.item(iid, "tags")
            self._tree.item(
                iid,
                tags=tuple(tag for tag in tags if tag != _SEARCH_MATCH_TAG),
            )
        self._search_highlight_iids.clear()

    def _highlight_search_matches(self, iids: Iterable[str]) -> None:
        for iid in iids:
            tags = self._tree.item(iid, "tags")
            if _SEARCH_MATCH_TAG not in tags:
                self._tree.item(iid, tags=(*tags, _SEARCH_MATCH_TAG))
            self._search_highlight_iids.add(iid)

    def _focus_search(self, _event: tk.Event | None = None) -> str:
        self._search_entry.focus_set()
        self._search_entry.selection_range(0, "end")
        return "break"

    def _find_matches(self, _event: tk.Event | None = None) -> None:
        query = self._search_var.get().strip()
        if self._prev_query == query:
            self._next_match()
            return

        self._clear_search_highlights()
        self._clear_details_search_highlight()
        self._prev_query = query
        if not query:
            self._search_pattern = None
            self._search_matches = []
            self._status_var.set("Enter a regular expression to search names, descriptions, metadata, or OIDs.")
            return

        try:
            self._search_pattern = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            self._search_pattern = None
            self._search_matches = []
            self._status_var.set(f"Invalid regular expression: {exc}")
            return

        matches_by_item: dict[str, tuple[int, ...]] = {}
        for oid, node in self._nodes_by_oid.items():
            iid = self._iid_by_oid.get(oid)
            display_label = self._tree.item(iid, "text") if iid else ""
            fields = [node.oid_text, node.label, node.kind, display_label]
            for definition in node.definitions:
                fields.extend(
                    (
                        definition.name,
                        definition.module,
                        definition.kind,
                        definition.syntax,
                        definition.access,
                        definition.status,
                        definition.description,
                    )
                )
            if iid and any(self._search_pattern.search(field) for field in fields):
                matches_by_item.setdefault(iid, oid)

        self._search_matches = sorted(matches_by_item.values())
        if not self._search_matches:
            self._status_var.set(f"No results for {self._search_var.get().strip()!r}.")
            return

        self._highlight_search_matches(matches_by_item)
        self._search_index = 0
        self._show_search_match()

    def _next_match(self) -> None:
        self._match(1)

    def _previous_match(self, _event: tk.Event | None = None) -> str:
        self._match(-1)
        return "break"

    def _match(self, direction: int) -> None:
        if not self._search_matches:
            return
        self._search_index = (self._search_index + direction) % len(self._search_matches)
        self._show_search_match()

    def _show_search_match(self) -> None:
        oid = self._search_matches[self._search_index]
        for depth in range(1, len(oid)):
            ancestor_iid = self._iid_by_oid.get(oid[:depth])
            if ancestor_iid:
                self._tree.item(ancestor_iid, open=True)
        iid = self._iid_by_oid[oid]
        self._tree.selection_set(iid)
        self._tree.focus(iid)
        self._tree.see(iid)
        self._show_selected_details()
        self._status_var.set(f"Match {self._search_index + 1} of {len(self._search_matches)}.")

    def _refresh_warnings(self) -> None:
        text = "\n".join(self._warnings) if self._warnings else "No parser warnings."
        self._set_text(self._warning_text, text)

    def _open_source_file(self, source_path: str) -> str:
        path = Path(source_path)
        if not path.is_file():
            messagebox.showerror(
                "Could not open MIB file",
                f"The source file is not available:\n{path}",
                parent=self,
            )
            return "break"

        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(
                "Could not open MIB file",
                f"Could not open {path}: {exc}",
                parent=self,
            )
        return "break"

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")


def main(startup_paths: Sequence[str | Path] = ()) -> None:
    MibExplorerApp(startup_paths=startup_paths).mainloop()
