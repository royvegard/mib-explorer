"""Tkinter interface for browsing locally compiled MIB definitions."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .model import OidDefinition, OidNode, build_oid_tree
from .parser import ParseResult, load_mibs


class MibExplorerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MIB Explorer")
        self.geometry("1120x720")
        self.minsize(760, 480)

        self._definitions: dict[tuple[str, str, tuple[int, ...]], OidDefinition] = {}
        self._warnings: list[str] = []
        self._nodes_by_oid: dict[tuple[int, ...], OidNode] = {}
        self._iid_by_oid: dict[tuple[int, ...], str] = {}
        self._oid_by_iid: dict[str, tuple[int, ...]] = {}
        self._search_matches: list[tuple[int, ...]] = []
        self._search_index = 0
        self._loading = False
        self._results: queue.Queue[tuple[ParseResult | None, Exception | None]] = queue.Queue()

        self._build_widgets()
        self.after(100, self._poll_results)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(4, weight=3)

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
        self._next_button.grid(row=0, column=5, padx=(6, 0))

        content = ttk.Panedwindow(self, orient="horizontal")
        content.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)

        tree_frame = ttk.Frame(content, padding=(0, 0, 5, 0))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self._tree = ttk.Treeview(tree_frame, columns=("oid", "kind"), selectmode="browse")
        self._tree.heading("#0", text="Name")
        self._tree.heading("oid", text="Numeric OID")
        self._tree.heading("kind", text="Type")
        self._tree.column("#0", width=210, minwidth=120, stretch=True)
        self._tree.column("oid", width=240, minwidth=160, stretch=True)
        self._tree.column("kind", width=115, minwidth=80, stretch=False)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)
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
        root = build_oid_tree(self._definitions.values())

        def insert(parent_iid: str, node: OidNode) -> None:
            iid = self._tree.insert(
                parent_iid,
                "end",
                text=node.label,
                values=(node.oid_text, node.kind),
                open=(not node.oid),
            )
            self._nodes_by_oid[node.oid] = node
            self._iid_by_oid[node.oid] = iid
            self._oid_by_iid[iid] = node.oid
            for arc in sorted(node.children):
                insert(iid, node.children[arc])

        insert("", root)

    def _show_selected_details(self, _event: tk.Event | None = None) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        oid = self._oid_by_iid.get(selection[0])
        if oid is None:
            return
        node = self._nodes_by_oid[oid]
        if not node.definitions:
            self._set_text(
                self._details,
                f"Structural OID node\n\nNumeric OID: {node.oid_text}\n"
                "No loaded MIB defines this exact node.",
            )
            return

        blocks: list[str] = []
        for definition in node.definitions:
            lines = [
                definition.name,
                f"Module: {definition.module}",
                f"Numeric OID: {definition.oid_text}",
            ]
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
            blocks.append("\n".join(lines))
        self._set_text(self._details, "\n\n" + ("\n\n" + "─" * 48 + "\n\n").join(blocks))

    def _find_matches(self, _event: tk.Event | None = None) -> None:
        needle = self._search_var.get().strip().casefold()
        if not needle:
            self._search_matches = []
            self._next_button.configure(state="disabled")
            self._status_var.set("Enter a name, description, or numeric OID to search.")
            return

        matches: list[tuple[int, ...]] = []
        for oid, node in self._nodes_by_oid.items():
            fields = [node.oid_text, node.label, node.kind]
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
            if needle in " ".join(fields).casefold():
                matches.append(oid)

        self._search_matches = sorted(matches)
        if not self._search_matches:
            self._next_button.configure(state="disabled")
            self._status_var.set(f"No results for {self._search_var.get().strip()!r}.")
            return

        self._next_button.configure(state="normal")
        self._search_index = 0
        self._show_search_match()

    def _next_match(self) -> None:
        if not self._search_matches:
            return
        self._search_index = (self._search_index + 1) % len(self._search_matches)
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

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")


def main() -> None:
    MibExplorerApp().mainloop()
