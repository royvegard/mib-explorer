from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from mib_explorer.model import OidDefinition
from mib_explorer.snmp import (
    SnmpValue,
    _format_var_bind,
    build_table_rows,
    format_timeticks,
    parse_target_settings,
    walk_subtree,
)


class FakePrintable:
    def __init__(self, value: str) -> None:
        self.value = value

    def prettyPrint(self) -> str:
        return self.value


class TimeTicks:
    def __init__(self, ticks: int) -> None:
        self.ticks = ticks

    def prettyPrint(self) -> str:
        return str(self.ticks)


class SnmpTargetSettingsTests(unittest.TestCase):
    def test_walk_can_be_cancelled_before_starting(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()
        batches = []

        result = walk_subtree(
            "agent.example",
            161,
            "public",
            (1, 3, 6),
            batches.append,
            cancel_event=cancel_event,
        )

        self.assertTrue(result.aborted)
        self.assertEqual(result.count, 0)
        self.assertEqual(batches, [])

    def test_walk_can_continue_past_the_starting_subtree(self) -> None:
        lexicographic_modes = []
        expected = SnmpValue(".1.3.6.1", "Integer32", "1")

        async def fake_batches(
            _host: str,
            _port: int,
            _community: str,
            _oid: tuple[int, ...],
            _batch_size: int,
            _cancel_event: threading.Event | None = None,
            *,
            lexicographic_mode: bool = False,
        ):
            lexicographic_modes.append(lexicographic_mode)
            yield (expected,)

        batches = []
        with patch("mib_explorer.snmp._walk_subtree_batches", fake_batches):
            result = walk_subtree(
                "agent.example",
                161,
                "public",
                (1, 3, 6),
                batches.append,
                walk_entire_mib=True,
            )

        self.assertEqual(lexicographic_modes, [True])
        self.assertEqual(batches, [(expected,)])
        self.assertEqual(result.count, 1)
        self.assertFalse(result.aborted)

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


class SnmpTableRowsTests(unittest.TestCase):
    def test_builds_rows_by_index_and_places_values_under_named_columns(self) -> None:
        columns = [
            OidDefinition(
                "ifIndex",
                (1, 3, 6, 1, 2, 1, 2, 2, 1, 1),
                "IF-MIB",
                kind="MibTableColumn",
            ),
            OidDefinition(
                "ifDescr",
                (1, 3, 6, 1, 2, 1, 2, 2, 1, 2),
                "IF-MIB",
                kind="MibTableColumn",
            ),
        ]
        values = [
            SnmpValue(".1.3.6.1.2.1.2.2.1.2.2", "OctetString", "eth1"),
            SnmpValue(".1.3.6.1.2.1.2.2.1.1.1", "Integer32", "1"),
            SnmpValue(".1.3.6.1.2.1.2.2.1.2.1", "OctetString", "eth0"),
            SnmpValue(".1.3.6.1.2.1.2.2.1.1.2", "Integer32", "2"),
        ]

        self.assertEqual(
            build_table_rows(values, columns),
            [("1", "1", "eth0"), ("2", "2", "eth1")],
        )


class SnmpFormattingTests(unittest.TestCase):
    def test_formats_timeticks_as_days_hours_minutes_and_decimal_seconds(self) -> None:
        self.assertEqual(
            format_timeticks(123456789),
            "14 days 6 hours 56 minutes 7.89 seconds",
        )

    def test_formats_timeticks_in_varbind_values(self) -> None:
        result = _format_var_bind(FakePrintable("1.3.6.1"), TimeTicks(123456789))

        self.assertEqual(result.syntax, "TimeTicks")
        self.assertEqual(result.value, "14 days 6 hours 56 minutes 7.89 seconds")

    def test_formats_exact_units_sensibly(self) -> None:
        self.assertEqual(format_timeticks(8_640_000), "1 day 0 hours 0 minutes 0.00 seconds")
        self.assertEqual(format_timeticks(100), "0 days 0 hours 0 minutes 1.00 second")

    def test_formats_numeric_oid_with_leading_dot_and_value_type(self) -> None:
        result = _format_var_bind(FakePrintable("1.3.6.1.2.1"), FakePrintable("42"))

        self.assertEqual(result, SnmpValue(".1.3.6.1.2.1", "FakePrintable", "42"))

    def test_does_not_duplicate_existing_leading_dot(self) -> None:
        result = _format_var_bind(FakePrintable(".1.3.6"), FakePrintable("up"))

        self.assertEqual(result.oid, ".1.3.6")


if __name__ == "__main__":
    unittest.main()
