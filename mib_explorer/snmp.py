"""Read-only SNMP subtree walks using PySNMP."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


class _Printable(Protocol):
    def prettyPrint(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SnmpValue:
    oid: str
    syntax: str
    value: str


def _format_var_bind(name: _Printable, value: _Printable) -> SnmpValue:
    numeric_oid = name.prettyPrint().lstrip(".")
    return SnmpValue(
        oid=f".{numeric_oid}",
        syntax=type(value).__name__,
        value=value.prettyPrint(),
    )


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
