"""Read-only SNMP subtree walks using PySNMP."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .model import OidDefinition, parse_oid


class _Printable(Protocol):
    def prettyPrint(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SnmpValue:
    oid: str
    syntax: str
    value: str


def format_timeticks(ticks: int) -> str:
    if ticks < 0:
        raise ValueError("TimeTicks cannot be negative")

    days, remainder = divmod(ticks, 8_640_000)
    hours, remainder = divmod(remainder, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    seconds_text = f"{seconds}.{centiseconds:02d}"
    seconds_unit = "second" if seconds == 1 and centiseconds == 0 else "seconds"
    return (
        f"{days} {'day' if days == 1 else 'days'} "
        f"{hours} {'hour' if hours == 1 else 'hours'} "
        f"{minutes} {'minute' if minutes == 1 else 'minutes'} "
        f"{seconds_text} {seconds_unit}"
    )


def _format_var_bind(name: _Printable, value: _Printable) -> SnmpValue:
    numeric_oid = name.prettyPrint().lstrip(".")
    syntax = type(value).__name__
    formatted_value = value.prettyPrint()
    if syntax.casefold() == "timeticks":
        try:
            formatted_value = format_timeticks(int(formatted_value))
        except ValueError:
            pass

    return SnmpValue(
        oid=f".{numeric_oid}",
        syntax=syntax,
        value=formatted_value,
    )


def build_table_rows(
    values: Iterable[SnmpValue],
    columns: Sequence[OidDefinition],
) -> list[tuple[str, ...]]:
    ordered_columns = tuple(sorted(columns, key=lambda definition: definition.oid))
    cells_by_index: dict[tuple[int, ...], dict[tuple[int, ...], str]] = {}

    for value in values:
        try:
            value_oid = parse_oid(value.oid)
        except ValueError:
            continue

        matching_columns = [
            definition
            for definition in ordered_columns
            if len(value_oid) > len(definition.oid)
            and value_oid[: len(definition.oid)] == definition.oid
        ]
        if not matching_columns:
            continue

        column = max(matching_columns, key=lambda definition: len(definition.oid))
        row_index = value_oid[len(column.oid) :]
        cells_by_index.setdefault(row_index, {})[column.oid] = value.value

    rows = []
    for row_index, cells in sorted(cells_by_index.items()):
        index_text = ".".join(str(arc) for arc in row_index)
        rows.append((index_text, *(cells.get(column.oid, "") for column in ordered_columns)))
    return rows


def parse_target_settings(host: str, port_text: str, community: str) -> tuple[str, int, str]:
    host = host.strip()
    if not host:
        raise ValueError("Enter the SNMP agent hostname or IP address.")

    try:
        port = int(port_text.strip())
    except ValueError as exc:
        raise ValueError("The SNMP port must be a number from 1 to 65535.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("The SNMP port must be between 1 and 65535.")
    if not community:
        raise ValueError("Enter the SNMPv2c community string.")

    return host, port, community


async def _walk_subtree(
    host: str,
    port: int,
    community: str,
    oid: tuple[int, ...],
) -> list[SnmpValue]:
    try:
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            walk_cmd,
        )
    except ImportError as exc:
        raise RuntimeError(
            "PySNMP is not available. Install the project requirements with "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    target = await UdpTransportTarget.create((host, port), timeout=2, retries=1)
    engine = SnmpEngine()
    results: list[SnmpValue] = []
    numeric_oid = ".".join(str(arc) for arc in oid)
    try:
        async for indication, status, index, var_binds in walk_cmd(
            engine,
            CommunityData(community, mpModel=1),
            target,
            ContextData(),
            ObjectType(ObjectIdentity(numeric_oid)),
            lexicographicMode=False,
            lookupMib=False,
        ):
            if indication:
                raise RuntimeError(str(indication))
            if status:
                raise RuntimeError(f"SNMP error {status} at varbind {index}")
            results.extend(_format_var_bind(name, value) for name, value in var_binds)
    finally:
        engine.close_dispatcher()

    return results


def get_subtree(
    host: str,
    port: int,
    community: str,
    oid: tuple[int, ...],
) -> list[SnmpValue]:
    """Fetch all accessible values below an OID using SNMPv2c GETNEXT walks."""

    return asyncio.run(_walk_subtree(host, port, community, oid))
