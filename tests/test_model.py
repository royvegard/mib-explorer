from __future__ import annotations

import unittest

from mib_explorer.model import OidDefinition, build_oid_tree, parse_oid


class OidModelTests(unittest.TestCase):
    def test_parse_oid_accepts_dotted_arcs(self) -> None:
        self.assertEqual(parse_oid(".1.3.6.1."), (1, 3, 6, 1))

    def test_parse_oid_rejects_symbolic_or_negative_arcs(self) -> None:
        with self.assertRaises(ValueError):
            parse_oid("1.3.iso")
        with self.assertRaises(ValueError):
            parse_oid("1.-3.6")

    def test_tree_creates_synthetic_parent_arcs_and_sorts(self) -> None:
        definition = OidDefinition("leaf", (1, 3, 6, 1, 4, 1), "TEST-MIB")
        root = build_oid_tree([definition])

        node = root
        for arc in definition.oid:
            node = node.children[arc]
        self.assertEqual(node.definitions, [definition])
        self.assertEqual(node.oid_text, "1.3.6.1.4.1")
        self.assertEqual(root.label, "OID tree")

    def test_node_combines_syntaxes_for_definitions_at_same_oid(self) -> None:
        first = OidDefinition("first", (1, 3, 6), "FIRST-MIB", syntax="INTEGER")
        second = OidDefinition("second", (1, 3, 6), "SECOND-MIB", syntax="OCTET STRING")
        node = build_oid_tree([first, second]).children[1].children[3].children[6]

        self.assertEqual(node.syntax, "INTEGER, OCTET STRING")

    def test_duplicate_definitions_are_not_duplicated(self) -> None:
        definition = OidDefinition("leaf", (1, 3, 6), "TEST-MIB")
        root = build_oid_tree([definition, definition])
        node = root.children[1].children[3].children[6]
        self.assertEqual(node.definitions, [definition])

    def test_distinct_names_at_same_oid_are_preserved(self) -> None:
        first = OidDefinition("first", (1, 3, 6), "FIRST-MIB")
        second = OidDefinition("second", (1, 3, 6), "SECOND-MIB")
        node = build_oid_tree([first, second]).children[1].children[3].children[6]
        self.assertEqual({definition.name for definition in node.definitions}, {"first", "second"})


if __name__ == "__main__":
    unittest.main()
