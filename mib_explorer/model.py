"""Data structures for normalized OID definitions and their tree."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class OidDefinition:
    """One named definition associated with a numeric OID."""

    name: str
    oid: tuple[int, ...]
    module: str
    kind: str = ""
    syntax: str = ""
    access: str = ""
    status: str = ""
    description: str = ""
    source_file: str = ""
    source_path: str = ""

    @property
    def oid_text(self) -> str:
        return f".{'.'.join(str(arc) for arc in self.oid)}" if self.oid else ""


@dataclass(slots=True)
class OidNode:
    """A numeric OID arc, which can have zero or more named definitions."""

    oid: tuple[int, ...]
    definitions: list[OidDefinition] = field(default_factory=list)
    children: dict[int, OidNode] = field(default_factory=dict)

    @property
    def oid_text(self) -> str:
        return f".{'.'.join(str(arc) for arc in self.oid)}" if self.oid else ""

    @property
    def label(self) -> str:
        if not self.oid:
            return "OID tree"
        if self.definitions:
            return self.definitions[0].name
        return str(self.oid[-1])

    @property
    def kind(self) -> str:
        kinds = sorted({definition.kind for definition in self.definitions if definition.kind})
        return ", ".join(kinds)

    @property
    def syntax(self) -> str:
        syntaxes = sorted({definition.syntax for definition in self.definitions if definition.syntax})
        return ", ".join(syntaxes)


def parse_oid(value: str | Iterable[int]) -> tuple[int, ...]:
    """Parse a dotted numeric OID into integer arcs."""

    if isinstance(value, str):
        parts = value.strip().strip(".").split(".")
        if not parts or parts == [""]:
            raise ValueError("OID must contain at least one numeric arc")
        try:
            arcs = tuple(int(part, 10) for part in parts)
        except ValueError as exc:
            raise ValueError(f"OID is not numeric: {value!r}") from exc
    else:
        arcs = tuple(int(part) for part in value)

    if not arcs:
        raise ValueError("OID must contain at least one numeric arc")
    if any(arc < 0 for arc in arcs):
        raise ValueError("OID arcs cannot be negative")
    return arcs


def build_oid_tree(definitions: Iterable[OidDefinition]) -> OidNode:
    """Create a tree, adding unnamed intermediate arcs when needed."""

    root = OidNode(())
    seen: set[tuple[str, str, tuple[int, ...]]] = set()

    ordered = sorted(definitions, key=lambda item: (item.oid, item.module, item.name))
    for definition in ordered:
        if not definition.oid:
            continue
        key = (definition.module, definition.name, definition.oid)
        if key in seen:
            continue
        seen.add(key)

        node = root
        for index, arc in enumerate(definition.oid, start=1):
            child = node.children.get(arc)
            if child is None:
                child = OidNode(definition.oid[:index])
                node.children[arc] = child
            node = child
        node.definitions.append(definition)

    return root
