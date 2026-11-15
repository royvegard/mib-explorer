from __future__ import annotations

import re
import unittest
from typing import Any

from mib_explorer.gui import MibExplorerApp
from mib_explorer.model import OidDefinition


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


class FakeButton:
    def configure(self, **_options: str) -> None:
        pass


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
        self._next_button = FakeButton()
        self._prev_button = FakeButton()
        self._status_var = FakeVariable("")
        self._search_matches: list[tuple[int, ...]] = []
        self._search_highlight_iids: set[str] = set()
        self._search_index = 0

    def _show_search_match(self) -> None:
        pass


class TreeControlTests(unittest.TestCase):
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
