from __future__ import annotations

import unittest

from mib_explorer.snmp import SnmpValue, _format_var_bind, parse_target_settings


class FakePrintable:
    def __init__(self, value: str) -> None:
        self.value = value

    def prettyPrint(self) -> str:
        return self.value


class SnmpTargetSettingsTests(unittest.TestCase):
    def test_strips_host_and_parses_port(self) -> None:
        self.assertEqual(
            parse_target_settings("  agent.example  ", " 1161 ", "private"),
            ("agent.example", 1161, "private"),
        )

    def test_rejects_missing_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "hostname or IP"):
            parse_target_settings("  ", "161", "public")

    def test_rejects_non_numeric_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "port must be a number"):
            parse_target_settings("agent.example", "snmp", "public")

    def test_rejects_port_outside_valid_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            parse_target_settings("agent.example", "65536", "public")

    def test_rejects_missing_community(self) -> None:
        with self.assertRaisesRegex(ValueError, "community string"):
            parse_target_settings("agent.example", "161", "")


class SnmpFormattingTests(unittest.TestCase):
    def test_formats_numeric_oid_with_leading_dot_and_value_type(self) -> None:
        result = _format_var_bind(FakePrintable("1.3.6.1.2.1"), FakePrintable("42"))

        self.assertEqual(result, SnmpValue(".1.3.6.1.2.1", "FakePrintable", "42"))

    def test_does_not_duplicate_existing_leading_dot(self) -> None:
        result = _format_var_bind(FakePrintable(".1.3.6"), FakePrintable("up"))

        self.assertEqual(result.oid, ".1.3.6")


if __name__ == "__main__":
    unittest.main()
