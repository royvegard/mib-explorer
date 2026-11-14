from __future__ import annotations

import unittest

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

    def item(self, iid: str, option: str | None = None, **options: object) -> object:
        if option is not None:
            return self.items[iid][option]
        return self.items[iid]


class FakeVariable:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class FakeButton:
    def configure(self, **_options: str) -> None:
        pass


class TreeRebuildHarness:
    _rebuild_tree = MibExplorerApp._rebuild_tree
    _find_matches = MibExplorerApp._find_matches

    def __init__(self, definitions: list[OidDefinition]) -> None:
        self._definitions = {
            (definition.module, definition.name, definition.oid): definition
            for definition in definitions
        }
        self._tree = RecordingTree()
        self._nodes_by_oid = {}
        self._iid_by_oid = {}
        self._oid_by_iid = {}
        self._definitions_by_iid = {}
        self._search_var = FakeVariable("org.dod.internet")
        self._next_button = FakeButton()
        self._search_matches: list[tuple[int, ...]] = []
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

        app._find_matches()
        self.assertEqual(len(app._search_matches), 1)


if __name__ == "__main__":
    unittest.main()
