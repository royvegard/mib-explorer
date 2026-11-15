"""Read-only SNMP subtree walks using PySNMP."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
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


@dataclass(frozen=True, slots=True)
class SnmpWalkResult:
    count: int
    aborted: bool


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


def table_cell_for_value(
    value: SnmpValue,
    columns: Sequence[OidDefinition],
) -> tuple[tuple[int, ...], tuple[int, ...], str] | None:
    try:
        value_oid = parse_oid(value.oid)
    except ValueError:
        return None

    matching_columns = [
        definition
        for definition in columns
        if len(value_oid) > len(definition.oid)
        and value_oid[: len(definition.oid)] == definition.oid
    ]
    if not matching_columns:
        return None

    column = max(matching_columns, key=lambda definition: len(definition.oid))
    return value_oid[len(column.oid) :], column.oid, value.value


def build_table_rows(
    values: Iterable[SnmpValue],
    columns: Sequence[OidDefinition],
) -> list[tuple[str, ...]]:
    ordered_columns = tuple(sorted(columns, key=lambda definition: definition.oid))
    cells_by_index: dict[tuple[int, ...], dict[tuple[int, ...], str]] = {}

    for value in values:
        cell = table_cell_for_value(value, ordered_columns)
        if cell is None:
            continue
        row_index, column_oid, cell_value = cell
        cells_by_index.setdefault(row_index, {})[column_oid] = cell_value

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


async def _walk_subtree_batches(
    host: str,
    port: int,
    community: str,
    oid: tuple[int, ...],
    batch_size: int,
    cancel_event: threading.Event | None = None,
    lexicographic_mode: bool = False,
) -> AsyncIterator[tuple[SnmpValue, ...] | None]:
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

    if cancel_event is not None and cancel_event.is_set():
        yield None
        return

    target = await UdpTransportTarget.create((host, port), timeout=2, retries=1)
    engine = SnmpEngine()
    pending: list[SnmpValue] = []
    numeric_oid = ".".join(str(arc) for arc in oid)
    walk_iterator = None
    try:
        walk_iterator = walk_cmd(
            engine,
            CommunityData(community, mpModel=1),
            target,
            ContextData(),
            ObjectType(ObjectIdentity(numeric_oid)),
            lexicographicMode=lexicographic_mode,
            lookupMib=False,
        ).__aiter__()
        while True:
            if cancel_event is None:
                try:
                    response = await walk_iterator.__anext__()
                except StopAsyncIteration:
                    break
            else:
                next_response = asyncio.ensure_future(walk_iterator.__anext__())
                while not next_response.done():
                    if cancel_event.is_set():
                        next_response.cancel()
                        await asyncio.gather(next_response, return_exceptions=True)
                        if pending:
                            yield tuple(pending)
                            pending.clear()
                        yield None
                        return
                    await asyncio.wait((next_response,), timeout=0.05)
                try:
                    response = next_response.result()
                except StopAsyncIteration:
                    break

            indication, status, index, var_binds = response
            if indication:
                raise RuntimeError(str(indication))
            if status:
                raise RuntimeError(f"SNMP error {status} at varbind {index}")
            pending.extend(_format_var_bind(name, value) for name, value in var_binds)
            if len(pending) >= batch_size:
                yield tuple(pending)
                pending.clear()
    except Exception:
        if pending:
            yield tuple(pending)
        raise
    finally:
        try:
            if walk_iterator is not None:
                close_iterator = getattr(walk_iterator, "aclose", None)
                if close_iterator is not None:
                    await close_iterator()
        finally:
            engine.close_dispatcher()

    if pending:
        yield tuple(pending)


async def _walk_subtree(
    host: str,
    port: int,
    community: str,
    oid: tuple[int, ...],
) -> list[SnmpValue]:
    results = []
    async for batch in _walk_subtree_batches(host, port, community, oid, 50):
        if batch is not None:
            results.extend(batch)
    return results


def get_subtree(
    host: str,
    port: int,
    community: str,
    oid: tuple[int, ...],
) -> list[SnmpValue]:
    """Fetch all accessible values below an OID using SNMPv2c GETNEXT walks."""

    return asyncio.run(_walk_subtree(host, port, community, oid))


def walk_subtree(
    host: str,
    port: int,
    community: str,
    oid: tuple[int, ...],
    on_batch: Callable[[tuple[SnmpValue, ...]], None],
    *,
    batch_size: int = 50,
    cancel_event: threading.Event | None = None,
    walk_entire_mib: bool = False,
) -> SnmpWalkResult:
    """Stream an OID walk; optionally continue past the starting subtree."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    async def walk_and_report() -> SnmpWalkResult:
        count = 0
        async for batch in _walk_subtree_batches(
            host,
            port,
            community,
            oid,
            batch_size,
            cancel_event,
            lexicographic_mode=walk_entire_mib,
        ):
            if batch is None:
                return SnmpWalkResult(count, aborted=True)
            on_batch(batch)
            count += len(batch)
        return SnmpWalkResult(count, aborted=False)

    return asyncio.run(walk_and_report())
