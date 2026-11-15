from __future__ import annotations

import queue
import re
import threading
import unittest
from types import SimpleNamespace
from typing import Any, cast

from mib_explorer.gui import (
    MibExplorerApp,
    _SnmpResultsBatch,
    _SnmpResultsComplete,
    _SnmpResultsFailure,
)
from mib_explorer.model import OidDefinition, OidNode
from mib_explorer.snmp import SnmpValue


class FakeTree:
    def __init__(self) -> None:
        self.children = {
            "": ["root"],
            "root": ["1"],
            "1": ["3"],
            "3": ["6"],
            "6": [],
        }
        self.open_states: dict[str, bool] = {}

    def get_children(self, item: str) -> list[str]:
        return self.children[item]

    def item(self, item: str, **options: bool) -> None:
        self.open_states[item] = options["open"]


class TreeControlHarness:
    _expand_all = MibExplorerApp._expand_all
    _collapse_all = MibExplorerApp._collapse_all
    _set_descendants_open = MibExplorerApp._set_descendants_open

    def __init__(self) -> None:
        self._tree = FakeTree()
        self._iid_by_oid = {(): "root"}


class RecordingTree:
    def __init__(self) -> None:
        self.children: dict[str, list[str]] = {"": []}
        self.items: dict[str, dict[str, object]] = {}

    def get_children(self, item: str) -> list[str]:
        return self.children[item]

    def delete(self, *_items: str) -> None:
        pass

    def insert(self, parent: str, _index: str, **options: object) -> str:
        iid = f"item-{len(self.items)}"
        self.items[iid] = options
        self.children.setdefault(parent, []).append(iid)
        self.children[iid] = []
        return iid

    def item(self, iid: str, option: str | None = None, **options: object) -> Any:
        if options:
            self.items[iid].update(options)
        if option is not None:
            return self.items[iid].get(option, ())
        return self.items[iid]


class FakeResultTree:
    def __init__(self, rows: list[tuple[str, ...]]) -> None:
        self.rows = rows

    def identify_row(self, _y: int) -> str:
        return "row"

    def get_children(self, _parent: str) -> list[int]:
        return list(range(len(self.rows)))

    def delete(self, *_items: int) -> None:
        self.rows.clear()

    def insert(self, _parent: str, _index: str, *, values: tuple[str, ...]) -> str:
        self.rows.append(values)
        return str(len(self.rows) - 1)

    def item(self, iid: str, *, values: tuple[str, ...]) -> None:
        self.rows[int(iid)] = values


class FakeStateButton:
    def __init__(self, state: str = "disabled") -> None:
        self.state = state

    def configure(self, *, state: str) -> None:
        self.state = state


class FakeTab:
    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class FakeNotebook:
    def __init__(self) -> None:
        self.forgotten: list[FakeTab] = []
        self.selected: object | None = None

    def forget(self, tab: FakeTab) -> None:
        self.forgotten.append(tab)

    def select(self, tab: object) -> None:
        self.selected = tab


class FakeDetails:
    def __init__(self, value: str) -> None:
        self.value = value
        self.highlight_ranges: list[tuple[int, int]] = []

    def tag_remove(self, _tag: str, _start: str, _end: str) -> None:
        self.highlight_ranges.clear()

    def get(self, _start: str, _end: str) -> str:
        return self.value

    def tag_add(self, _tag: str, start: str, end: str) -> None:
        self.highlight_ranges.append((self._offset(start), self._offset(end)))

    def _offset(self, index: str) -> int:
        line, column = (int(part) for part in index.split("."))
        lines = self.value.splitlines(keepends=True)
        return sum(len(part) for part in lines[: line - 1]) + column


class FakeVariable:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value



class FakeSearchEntry:
    def __init__(self) -> None:
        self.focused = False
        self.selection: tuple[int, str] | None = None

    def focus_set(self) -> None:
        self.focused = True

    def selection_range(self, start: int, end: str) -> None:
        self.selection = (start, end)


class SearchFocusHarness:
    _focus_search = MibExplorerApp._focus_search

    def __init__(self) -> None:
        self._search_entry = FakeSearchEntry()


class PreviousMatchHarness:
    _previous_match = MibExplorerApp._previous_match

    def _match(self, direction: int) -> None:
        self.direction = direction


class OidNameHarness:
    _oid_name_for_text = MibExplorerApp._oid_name_for_text
    _oid_name_for_result = MibExplorerApp._oid_name_for_result
    _result_oid_is_defined = MibExplorerApp._result_oid_is_defined

    def __init__(self) -> None:
        definitions = (
            OidDefinition(
                "system",
                (1, 3, 6, 1, 2, 1, 1),
                "RFC1213-MIB",
                kind="objectidentity",
            ),
            OidDefinition(
                "sysDescr",
                (1, 3, 6, 1, 2, 1, 1, 1),
                "RFC1213-MIB",
                kind="scalar",
            ),
            OidDefinition(
                "ifDescr",
                (1, 3, 6, 1, 2, 1, 2, 2, 1, 2),
                "IF-MIB",
                kind="MibTableColumn",
            ),
        )
        self._nodes_by_oid = {
            definition.oid: OidNode(definition.oid, definitions=[definition])
            for definition in definitions
        }


class FakeContextTree:
    def identify_row(self, _y: int) -> str:
        return "node"

    def selection_set(self, _iid: str) -> None:
        pass

    def focus(self, _iid: str) -> None:
        pass

    def selection(self) -> tuple[str, ...]:
        return ("node",)


class FakeContextMenu:
    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    def delete(self, _start: int, _end: str) -> None:
        self.commands.clear()

    def add_command(self, **options: Any) -> None:
        self.commands.append(options)

    def tk_popup(self, _x: int, _y: int) -> None:
        pass


class WalkContextMenuHarness:
    _show_tree_context_menu = MibExplorerApp._show_tree_context_menu
    _walk_selected_oid = MibExplorerApp._walk_selected_oid

    def __init__(self) -> None:
        self._tree = FakeContextTree()
        self._tree_context_menu = FakeContextMenu()
        self._oid_by_iid = {"node": (1, 3, 6)}
        self._snmp_host_var = FakeVariable("agent.example")
        self._snmp_port_var = FakeVariable("161")
        self._snmp_community_var = FakeVariable("public")
        self._opened: tuple[tuple[Any, ...], dict[str, Any]] | None = None

    def _table_definition_for_iid(self, _iid: str) -> None:
        return None

    def _get_selected_subtree(self) -> None:
        pass

    def _open_snmp_results_tab(self, *args: Any, **kwargs: Any) -> None:
        self._opened = (args, kwargs)


class SnmpResultsPollingHarness:
    _poll_snmp_results = MibExplorerApp._poll_snmp_results
    _abort_snmp_request = MibExplorerApp._abort_snmp_request
    _close_snmp_tab = MibExplorerApp._close_snmp_tab
    _append_snmp_values = MibExplorerApp._append_snmp_values
    _finish_snmp_results = staticmethod(MibExplorerApp._finish_snmp_results)
    _oid_name_for_text = MibExplorerApp._oid_name_for_text
    _oid_name_for_result = MibExplorerApp._oid_name_for_result
    _result_oid_is_defined = MibExplorerApp._result_oid_is_defined
    _clear_snmp_result_view = staticmethod(MibExplorerApp._clear_snmp_result_view)

    def __init__(self, rows: list[tuple[str, ...]]) -> None:
        self._snmp_results: queue.Queue[
            _SnmpResultsBatch | _SnmpResultsComplete | _SnmpResultsFailure
        ] = queue.Queue()
        self._nodes_by_oid = {}
        self.tree = FakeResultTree(rows)
        self.button = FakeStateButton()
        self.abort_button = FakeStateButton("normal")
        self.tab = FakeTab()
        self._notebook = FakeNotebook()
        self.view = SimpleNamespace(
            tree=self.tree,
            status_var=FakeVariable(""),
            loading=True,
            update_button=self.button,
            abort_button=self.abort_button,
            cancel_event=threading.Event(),
            tab=self.tab,
            table_columns=None,
            received_count=0,
            table_row_iids={},
            table_row_cells={},
            oid_by_row_iid={},
        )
        self._snmp_views = {1: self.view}


class FakeNavigationTree:
    def __init__(self) -> None:
        self.parents = {"leaf": "branch", "branch": "root", "root": ""}
        self.open_states: dict[str, bool] = {}
        self.selected: str | None = None
        self.focused: str | None = None
        self.seen: str | None = None

    def parent(self, iid: str) -> str:
        return self.parents[iid]

    def item(self, iid: str, **options: bool) -> None:
        self.open_states[iid] = options["open"]

    def selection_set(self, iid: str) -> None:
        self.selected = iid

    def focus(self, iid: str) -> None:
        self.focused = iid

    def see(self, iid: str) -> None:
        self.seen = iid


class OidNavigationHarness:
    _show_result_oid = MibExplorerApp._show_result_oid
    _navigate_to_oid = MibExplorerApp._navigate_to_oid

    def __init__(self) -> None:
        self._snmp_views = {
            1: SimpleNamespace(
                tree=FakeResultTree([]),
                oid_by_row_iid={"row": (1, 3, 6, 1, 2, 1)},
            )
        }
        self._iid_by_oid = {(1,): "root", (1, 3): "branch", (1, 3, 6): "leaf"}
        self._tree = FakeNavigationTree()
        self._notebook = FakeNotebook()
        self._browser_tab = object()
        self.details_updated = False

    def _show_selected_details(self) -> None:
        self.details_updated = True


class TableDefinitionHarness:
    _kind_is = staticmethod(MibExplorerApp._kind_is)
    _table_definition_for_iid = MibExplorerApp._table_definition_for_iid
    _table_column_definitions = MibExplorerApp._table_column_definitions

    def __init__(self) -> None:
        self.table = OidDefinition("sampleTable", (1, 3, 6, 1, 4, 1, 999, 1), "TEST-MIB", kind="table")
        row = OidDefinition("sampleEntry", (*self.table.oid, 1), "TEST-MIB", kind="row")
        index = OidDefinition("sampleIndex", (*row.oid, 1), "TEST-MIB", kind="column")
        value = OidDefinition("sampleValue", (*row.oid, 2), "TEST-MIB", kind="column")
        self._definitions_by_iid = {"table": (self.table,), "row": (row,)}
        self._definitions = {
            (definition.module, definition.name, definition.oid): definition
            for definition in (self.table, row, index, value)
        }


class TreeRebuildHarness:
    _rebuild_tree = MibExplorerApp._rebuild_tree
    _clear_search_highlights = MibExplorerApp._clear_search_highlights
    _highlight_search_matches = MibExplorerApp._highlight_search_matches
    _clear_details_search_highlight = MibExplorerApp._clear_details_search_highlight
    _highlight_details_matches = MibExplorerApp._highlight_details_matches
    _text_index_for_offset = staticmethod(MibExplorerApp._text_index_for_offset)
    _find_matches = MibExplorerApp._find_matches

    def __init__(self, definitions: list[OidDefinition]) -> None:
        self._definitions = {
            (definition.module, definition.name, definition.oid): definition
            for definition in definitions
        }
        self._tree = RecordingTree()
        self._details = FakeDetails("needle NEEDLE needle")
        self._nodes_by_oid = {}
        self._iid_by_oid = {}
        self._oid_by_iid = {}
        self._definitions_by_iid = {}
        self._search_var = FakeVariable(r"^org\.dod\.internet$")
        self._search_pattern: re.Pattern[str] | None = None
        self._status_var = FakeVariable("")
        self._search_matches: list[tuple[int, ...]] = []
        self._search_highlight_iids: set[str] = set()
        self._search_index = 0
        self._prev_query = ""

    def _show_search_match(self) -> None:
        pass


class TreeControlTests(unittest.TestCase):
    def test_ctrl_f_focuses_search_and_selects_its_contents(self) -> None:
        app = SearchFocusHarness()

        result = app._focus_search()

        self.assertEqual(result, "break")
        self.assertTrue(app._search_entry.focused)
        self.assertEqual(app._search_entry.selection, (0, "end"))

    def test_shift_return_runs_previous_match_handler(self) -> None:
        app = PreviousMatchHarness()

        result = app._previous_match(cast(Any, object()))

        self.assertEqual(app.direction, -1)
        self.assertEqual(result, "break")

    def test_resolves_exact_snmp_oid_name(self) -> None:
        app = OidNameHarness()

        self.assertEqual(app._oid_name_for_text(".1.3.6.1.2.1.1.1"), "sysDescr")

    def test_resolves_snmp_instance_using_longest_known_oid_name(self) -> None:
        app = OidNameHarness()

        self.assertEqual(app._oid_name_for_text(".1.3.6.1.2.1.1.1.0"), "sysDescr.0")

    def test_unknown_snmp_oid_uses_numeric_oid_as_name(self) -> None:
        app = OidNameHarness()

        self.assertEqual(app._oid_name_for_text(".1.3.6.1.99"), ".1.3.6.1.99")

    def test_unresolved_result_oid_name_has_an_asterisk(self) -> None:
        app = OidNameHarness()

        self.assertEqual(app._oid_name_for_result(".1.3.6.1.2.1.1.1"), "sysDescr")
        self.assertEqual(app._oid_name_for_result(".1.3.6.1.2.1.1.1.0"), "sysDescr.0")
        self.assertEqual(app._oid_name_for_result(".1.3.6.1.2.1.1.8.1"), "system.8.1*")
        self.assertEqual(app._oid_name_for_result(".1.3.6.1.2.1.1.8.0"), "system.8.0*")
        self.assertEqual(
            app._oid_name_for_result(".1.3.6.1.2.1.2.2.1.2.7"),
            "ifDescr.7",
        )
        self.assertEqual(app._oid_name_for_result(".1.3.6.1.99"), ".1.3.6.1.99*")

    def test_double_clicking_result_row_selects_nearest_mib_node(self) -> None:
        app = OidNavigationHarness()

        result = app._show_result_oid(1, cast(Any, SimpleNamespace(y=12)))

        self.assertEqual(result, "break")
        self.assertEqual(app._notebook.selected, app._browser_tab)
        self.assertEqual(app._tree.selected, "leaf")
        self.assertEqual(app._tree.focused, "leaf")
        self.assertEqual(app._tree.seen, "leaf")
        self.assertEqual(app._tree.open_states, {"branch": True, "root": True})
        self.assertTrue(app.details_updated)

    def test_context_menu_walk_starts_at_selected_oid_and_runs_to_mib_end(self) -> None:
        app = WalkContextMenuHarness()

        result = app._show_tree_context_menu(
            cast(Any, SimpleNamespace(y=20, x_root=100, y_root=200))
        )

        self.assertEqual(result, "break")
        self.assertEqual(
            [command["label"] for command in app._tree_context_menu.commands],
            ["Get subtree", "Walk"],
        )
        walk_command = app._tree_context_menu.commands[1]
        self.assertEqual(walk_command["state"], "normal")
        walk_command["command"]()
        self.assertEqual(
            app._opened,
            (("agent.example", 161, "public", (1, 3, 6)), {"walk_entire_mib": True}),
        )

    def test_table_definition_is_recognized_but_table_row_is_not(self) -> None:
        app = TableDefinitionHarness()

        self.assertIs(app._table_definition_for_iid("table"), app.table)
        self.assertIsNone(app._table_definition_for_iid("row"))

    def test_snmp_refresh_populates_rows_as_batches_arrive(self) -> None:
        app = SnmpResultsPollingHarness([("old", "old")])
        app._clear_snmp_result_view(cast(Any, app.view))
        app._snmp_results.put(
            _SnmpResultsBatch(1, (SnmpValue(".1.3.6", "Integer32", "42"),))
        )

        app._poll_snmp_results()

        self.assertEqual(app.tree.rows, [(".1.3.6*", ".1.3.6", "Integer32", "42")])
        self.assertEqual(app.view.oid_by_row_iid, {"0": (1, 3, 6)})
        self.assertEqual(app.view.status_var.value, "Received 1 value(s)…")
        self.assertTrue(app.view.loading)

        app._snmp_results.put(_SnmpResultsComplete(1, 1))
        app._poll_snmp_results()

        self.assertEqual(app.view.status_var.value, "Received 1 value(s).")
        self.assertEqual(app.button.state, "normal")
        self.assertEqual(app.abort_button.state, "disabled")
        self.assertFalse(app.view.loading)

    def test_snmp_abort_keeps_fetched_rows_and_reports_aborted_state(self) -> None:
        app = SnmpResultsPollingHarness([])
        app._snmp_results.put(
            _SnmpResultsBatch(1, (SnmpValue(".1.3.6", "Integer32", "42"),))
        )
        app._snmp_results.put(_SnmpResultsComplete(1, 1, aborted=True))

        app._poll_snmp_results()

        self.assertEqual(app.tree.rows, [(".1.3.6*", ".1.3.6", "Integer32", "42")])
        self.assertEqual(app.view.status_var.value, "Read aborted after 1 value(s).")
        self.assertEqual(app.button.state, "normal")
        self.assertEqual(app.abort_button.state, "disabled")
        self.assertFalse(app.view.loading)

    def test_abort_button_signals_cancellation(self) -> None:
        app = SnmpResultsPollingHarness([])

        app._abort_snmp_request(1)

        self.assertTrue(app.view.cancel_event.is_set())
        self.assertEqual(app.abort_button.state, "disabled")
        self.assertEqual(
            app.view.status_var.value,
            "Aborting SNMP walk; keeping fetched values…",
        )

    def test_closing_tab_cancels_read_and_discards_queued_values(self) -> None:
        app = SnmpResultsPollingHarness([])
        app._snmp_results.put(
            _SnmpResultsBatch(1, (SnmpValue(".1.3.6", "Integer32", "42"),))
        )

        app._close_snmp_tab(1)
        app._poll_snmp_results()

        self.assertTrue(app.view.cancel_event.is_set())
        self.assertTrue(app.tab.destroyed)
        self.assertEqual(app._notebook.forgotten, [app.tab])
        self.assertEqual(app.tree.rows, [])

    def test_snmp_refresh_error_keeps_partial_results(self) -> None:
        app = SnmpResultsPollingHarness([("old", "row")])
        app._clear_snmp_result_view(cast(Any, app.view))
        app._snmp_results.put(
            _SnmpResultsBatch(1, (SnmpValue(".1.3.6", "Integer32", "42"),))
        )
        app._snmp_results.put(_SnmpResultsFailure(1, "timed out"))

        app._poll_snmp_results()

        self.assertEqual(app.tree.rows, [(".1.3.6*", ".1.3.6", "Integer32", "42")])
        self.assertEqual(app.view.status_var.value, "SNMP walk failed: timed out")
        self.assertEqual(app.button.state, "normal")
        self.assertFalse(app.view.loading)

    def test_table_batches_fill_the_same_row_incrementally(self) -> None:
        app = SnmpResultsPollingHarness([])
        column_index = OidDefinition("sampleIndex", (1, 3, 6, 1), "TEST-MIB")
        column_value = OidDefinition("sampleValue", (1, 3, 6, 2), "TEST-MIB")
        app.view.table_columns = (column_index, column_value)
        app._snmp_results.put(
            _SnmpResultsBatch(1, (SnmpValue(".1.3.6.1.7", "Integer32", "7"),))
        )

        app._poll_snmp_results()
        self.assertEqual(app.tree.rows, [("7", "7", "")])

        app._snmp_results.put(
            _SnmpResultsBatch(1, (SnmpValue(".1.3.6.2.7", "OctetString", "ready"),))
        )
        app._poll_snmp_results()

        self.assertEqual(app.tree.rows, [("7", "7", "ready")])
        self.assertEqual(app.view.table_row_iids, {(7,): "0"})
        self.assertEqual(app.view.oid_by_row_iid, {"0": (1, 3, 6, 1, 7)})

    def test_table_column_definitions_are_collected_under_row_oid(self) -> None:
        app = TableDefinitionHarness()

        columns = app._table_column_definitions(app.table)

        self.assertEqual([definition.name for definition in columns], ["sampleIndex", "sampleValue"])

    def test_expand_all_opens_every_tree_item(self) -> None:
        app = TreeControlHarness()

        app._expand_all()

        self.assertEqual(app._tree.open_states, {"root": True, "1": True, "3": True, "6": True})

    def test_collapse_all_closes_descendants_but_keeps_root_visible(self) -> None:
        app = TreeControlHarness()

        app._collapse_all()

        self.assertEqual(app._tree.open_states, {"1": False, "3": False, "6": False, "root": True})

    def test_details_highlight_every_case_insensitive_regex_match(self) -> None:
        app = TreeRebuildHarness([])
        app._details = FakeDetails("foo12 FOO3 foo")
        app._search_pattern = re.compile(r"foo\d+", re.IGNORECASE)

        app._highlight_details_matches()

        self.assertEqual(app._details.highlight_ranges, [(0, 5), (6, 10)])

    def test_invalid_regular_expression_is_reported(self) -> None:
        app = TreeRebuildHarness([])
        app._search_var.set("[")

        app._find_matches()

        self.assertEqual(app._search_matches, [])
        self.assertIn("Invalid regular expression", app._status_var.value)

    def test_single_child_chains_are_combined_and_keep_all_definitions(self) -> None:
        definitions = [
            OidDefinition("org", (1,), "TEST-MIB"),
            OidDefinition("dod", (1, 3), "TEST-MIB"),
            OidDefinition("internet", (1, 3, 6), "TEST-MIB"),
            OidDefinition("mgmt", (1, 3, 6, 1), "TEST-MIB"),
            OidDefinition("private", (1, 3, 6, 2), "TEST-MIB"),
        ]
        app = TreeRebuildHarness(definitions)

        app._rebuild_tree()

        iid = app._iid_by_oid[(1,)]
        self.assertEqual(iid, app._iid_by_oid[(1, 3)])
        self.assertEqual(iid, app._iid_by_oid[(1, 3, 6)])
        self.assertEqual(app._tree.items[iid]["text"], "org.dod.internet")
        self.assertEqual(app._oid_by_iid[iid], (1, 3, 6))
        self.assertEqual(app._definitions_by_iid[iid], tuple(definitions[:3]))
        self.assertIn("search_match", app._tree.item(iid, "tags"))

        app._search_var.set("no-such-node")
        app._find_matches()
        self.assertEqual(app._search_matches, [])
        self.assertNotIn("search_match", app._tree.item(iid, "tags"))


if __name__ == "__main__":
    unittest.main()
