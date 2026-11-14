from __future__ import annotations

import unittest
from pathlib import Path

from mib_explorer.parser import load_mibs

FIXTURES = Path(__file__).parent / "fixtures"


class MibParserTests(unittest.TestCase):
    def test_compiles_mib_file_and_extracts_oid_metadata(self) -> None:
        result = load_mibs([FIXTURES / "TEST-MIB.mib"])
        definitions = {(definition.module, definition.name): definition for definition in result.definitions}

        self.assertIn("TEST-MIB", result.modules)
        scalar = definitions[("TEST-MIB", "testScalar")]
        self.assertEqual(scalar.oid, (1, 3, 6, 1, 4, 1, 55555, 1, 1))
        self.assertEqual(scalar.syntax, "INTEGER")
        self.assertEqual(
            scalar.description,
            "A sample scalar for parser tests.\n"
            "This second line should remain separate.\n\n"
            "This paragraph should also remain separate.\n\n"
            "This paragraph contains some indented text:\n"
            "        This is indented\n"
            "    So is this line\n\n"
            "Final paragraph.",
        )

    def test_compiles_smi_v1_file_with_smi_extension(self) -> None:
        result = load_mibs([FIXTURES / "LEGACY-MIB.smi"])
        definitions = {(definition.module, definition.name): definition for definition in result.definitions}

        self.assertIn("LEGACY-MIB", result.modules)
        self.assertEqual(definitions[("LEGACY-MIB", "legacyLeaf")].oid, (1, 3, 6, 1, 4, 1, 55556, 1))

    def test_directory_load_compiles_supported_files(self) -> None:
        result = load_mibs([FIXTURES])
        self.assertEqual(set(result.modules), {"LEGACY-MIB", "TEST-MIB"})
        self.assertGreaterEqual(len(result.definitions), 5)

    def test_missing_path_is_reported(self) -> None:
        result = load_mibs([FIXTURES / "missing.mib"])
        self.assertFalse(result.definitions)
        self.assertTrue(any("does not exist" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
