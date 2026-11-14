from __future__ import annotations

import unittest

from mib_explorer.gui import MibExplorerApp


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


class TreeControlTests(unittest.TestCase):
    def test_expand_all_opens_every_tree_item(self) -> None:
        app = TreeControlHarness()

        app._expand_all()

        self.assertEqual(app._tree.open_states, {"root": True, "1": True, "3": True, "6": True})

    def test_collapse_all_closes_descendants_but_keeps_root_visible(self) -> None:
        app = TreeControlHarness()

        app._collapse_all()

        self.assertEqual(app._tree.open_states, {"1": False, "3": False, "6": False, "root": True})


if __name__ == "__main__":
    unittest.main()
