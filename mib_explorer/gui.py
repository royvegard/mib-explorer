"""Tkinter interface for browsing locally compiled MIB definitions."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .model import OidDefinition, OidNode, build_oid_tree
from .parser import ParseResult, load_mibs


def _single_child_chain(node: OidNode) -> tuple[OidNode, ...]:
    chain = [node]
    while len(chain[-1].children) == 1:
        chain.append(next(iter(chain[-1].children.values())))
    return tuple(chain)


_DARK_THEME = "dark"
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
        self._search_index = 0
        self._loading = False
        self._source_link_tags: list[str] = []
        self._results: queue.Queue[tuple[ParseResult | None, Exception | None]] = queue.Queue()

        self._build_widgets()
        self._set_theme("dark")
        self.after(100, self._poll_results)
        if startup_paths:
            self._start_loading(startup_paths)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

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
        self._find_button = ttk.Button(toolbar, text="Find", command=self._find_matches)
        self._find_button.grid(row=0, column=4, sticky="w", padx=(6, 0))
        self._next_button = ttk.Button(toolbar, text="Next", command=self._next_match, state="disabled")
        self._next_button.grid(row=0, column=5, sticky="w", padx=(6, 0))
        self._prev_button = ttk.Button(toolbar, text="Prev", command=self._previous_match, state="disabled")
        self._prev_button.grid(row=0, column=6, sticky="w", padx=(6, 0))
        ttk.Button(toolbar, text="Expand all", command=self._expand_all).grid(
            row=0, column=7, padx=(12, 0)
        )
        ttk.Button(toolbar, text="Collapse all", command=self._collapse_all).grid(
            row=0, column=8, padx=(6, 0)
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
        self._theme_button.grid(row=0, column=9, padx=(10, 0))

        content = ttk.Panedwindow(self, orient="horizontal")
        content.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)

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
        self._tree.column("#0", width=300, minwidth=300, stretch=True)
        self._tree.column("oid", width=180, minwidth=110, stretch=True)
        self._tree.column("kind", width=100, minwidth=70, stretch=True)
        self._tree.column("syntax", width=100, minwidth=70, stretch=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        tree_xscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=tree_scroll.set, xscrollcommand=tree_xscroll.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self._tree.bind("<<TreeviewSelect>>", self._show_selected_details)
        content.add(tree_frame, weight=3)

        details_frame = ttk.LabelFrame(content, text="Definition details", padding=6)
        details_frame.rowconfigure(0, weight=1)
        details_frame.columnconfigure(0, weight=1)
        self._details = tk.Text(details_frame, wrap="word", state="disabled", padx=7, pady=6)
        details_scroll = ttk.Scrollbar(details_frame, orient="vertical", command=self._details.yview)
        self._details.configure(yscrollcommand=details_scroll.set)
        self._details.grid(row=0, column=0, sticky="nsew")
        details_scroll.grid(row=0, column=1, sticky="ns")
        content.add(details_frame, weight=2)

        self._status_var = tk.StringVar(value="Open MIB/SMI files or a folder to get started.")
        ttk.Label(self, textvariable=self._status_var, anchor="w", padding=(8, 3)).grid(
            row=2, column=0, sticky="ew"
        )

        warning_frame = ttk.LabelFrame(self, text="Parser warnings", padding=(6, 3))
        warning_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        warning_frame.columnconfigure(0, weight=1)
        self._warning_text = tk.Text(warning_frame, height=4, wrap="word", state="disabled")
        warning_scroll = ttk.Scrollbar(warning_frame, orient="vertical", command=self._warning_text.yview)
        self._warning_text.configure(yscrollcommand=warning_scroll.set)
        self._warning_text.grid(row=0, column=0, sticky="ew")
        warning_scroll.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(3, weight=0)
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

        if not definitions:
            self._set_text(
                self._details,
                f"Structural OID node\n\nNumeric OID: {node.oid_text}\n"
                "No loaded MIB defines this exact node.",
            )
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

    def _find_matches(self, _event: tk.Event | None = None) -> None:
        needle = self._search_var.get().strip().casefold()
        if not needle:
            self._search_matches = []
            self._next_button.configure(state="disabled")
            self._prev_button.configure(state="disabled")
            self._status_var.set("Enter a name, description, or numeric OID to search.")
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
            if iid and needle in " ".join(fields).casefold():
                matches_by_item.setdefault(iid, oid)

        self._search_matches = sorted(matches_by_item.values())
        if not self._search_matches:
            self._next_button.configure(state="disabled")
            self._prev_button.configure(state="disabled")
            self._status_var.set(f"No results for {self._search_var.get().strip()!r}.")
            return

        self._next_button.configure(state="normal")
        self._prev_button.configure(state="normal")
        self._search_index = 0
        self._show_search_match()

    def _next_match(self) -> None:
        self._match(1)

    def _previous_match(self) -> None:
        self._match(-1)

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
