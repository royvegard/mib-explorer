"""PySMI-backed loading and normalization for local MIB/SMI files."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pysmi import error as pysmi_error
from pysmi.codegen import JsonCodeGen
from pysmi.compiler import MibCompiler
from pysmi.parser import SmiStarParser
from pysmi.reader import FileReader
from pysmi.writer import CallbackWriter

from .model import OidDefinition, parse_oid

SUPPORTED_SUFFIXES = {".mib", ".smi", ".txt", ".my"}
PYSMI_EXTENSIONS = (
    "",
    ".txt",
    ".mib",
    ".my",
    ".smi",
    ".TXT",
    ".MIB",
    ".MY",
    ".SMI",
)


@dataclass(frozen=True, slots=True)
class ParseResult:
    definitions: tuple[OidDefinition, ...]
    modules: tuple[str, ...]
    warnings: tuple[str, ...]


def _collect_files(paths: Iterable[str | Path]) -> tuple[list[Path], list[Path], list[str]]:
    files: set[Path] = set()
    search_dirs: set[Path] = set()
    warnings: list[str] = []

    for supplied_path in paths:
        path = Path(supplied_path).expanduser()
        if not path.exists():
            warnings.append(f"Path does not exist: {path}")
            continue

        path = path.resolve()
        if path.is_dir():
            search_dirs.add(path)
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES:
                    files.add(candidate.resolve())
        elif path.is_file():
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                warnings.append(f"Unsupported file extension: {path.name}")
                continue
            files.add(path)
            search_dirs.add(path.parent)

    return sorted(files), sorted(search_dirs), warnings


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("type") or value.get("name") or json.dumps(value, sort_keys=True))
    if isinstance(value, list):
        return ", ".join(str(part) for part in value)
    return str(value)


def _module_definitions(module: str, source: str) -> tuple[list[OidDefinition], list[str]]:
    definitions: list[OidDefinition] = []
    warnings: list[str] = []
    try:
        document = json.loads(source)
    except (TypeError, json.JSONDecodeError) as exc:
        return [], [f"PySMI returned invalid JSON for {module}: {exc}"]

    if not isinstance(document, dict):
        return [], [f"PySMI returned an unexpected document for {module}"]

    for symbol, entry in document.items():
        if not isinstance(entry, dict) or not entry.get("oid"):
            continue
        try:
            oid = parse_oid(str(entry["oid"]))
        except ValueError as exc:
            warnings.append(f"Skipped {module}::{symbol}: {exc}")
            continue

        definitions.append(
            OidDefinition(
                name=str(entry.get("name") or symbol),
                oid=oid,
                module=module,
                kind=str(entry.get("nodetype") or entry.get("class") or ""),
                syntax=_display_value(entry.get("syntax")),
                access=str(entry.get("maxaccess") or entry.get("access") or ""),
                status=_display_value(entry.get("status")),
                description=str(entry.get("description") or ""),
            )
        )

    return definitions, warnings


def load_mibs(paths: Iterable[str | Path]) -> ParseResult:
    """Compile selected local files and imported local MIBs through PySMI.

    PySMI locates modules by their declared module name, so filenames are
    expected to use the module name (case-insensitive), as is conventional
    for MIB repositories. Imported modules are searched in the selected
    files' directories and recursively below selected directories.
    """

    files, search_dirs, warnings = _collect_files(paths)
    if not files:
        if not warnings:
            warnings.append("No .mib, .smi, .txt, or .my files were found.")
        return ParseResult((), (), tuple(warnings))

    module_names: list[str] = []
    seen_module_names: set[str] = set()
    for path in files:
        module_name = path.stem
        folded_name = module_name.casefold()
        if folded_name not in seen_module_names:
            module_names.append(module_name)
            seen_module_names.add(folded_name)

    generated: dict[str, str] = {}

    def collect_json(module: str, contents: str, context: dict[str, str]) -> None:
        context[module] = contents

    compiler = MibCompiler(
        SmiStarParser(),
        JsonCodeGen(),
        CallbackWriter(collect_json, generated),
    )
    readers = [
        FileReader(str(directory), recursive=True).set_options(exts=PYSMI_EXTENSIONS)
        for directory in search_dirs
    ]
    compiler.add_sources(*readers)

    try:
        statuses = compiler.compile(*module_names, ignoreErrors=True, genTexts=True)
    except (pysmi_error.PySmiError, OSError, ValueError) as exc:
        return ParseResult((), (), tuple(warnings + [f"PySMI could not compile the selected files: {exc}"]))

    for module, status in statuses.items():
        status_text = str(status)
        if status_text in {"failed", "missing", "unprocessed"}:
            detail = getattr(status, "error", None)
            suffix = f": {detail}" if detail else ""
            warnings.append(f"{module}: {status_text}{suffix}")

    definitions: list[OidDefinition] = []
    for module, document in sorted(generated.items()):
        module_records, module_warnings = _module_definitions(module, document)
        definitions.extend(module_records)
        warnings.extend(module_warnings)

    if not generated:
        warnings.append(
            "PySMI produced no compiled modules. Check that filenames match MIB module names "
            "and that required imported MIBs are available locally."
        )

    unique: dict[tuple[str, str, tuple[int, ...]], OidDefinition] = {}
    for definition in definitions:
        unique[(definition.module, definition.name, definition.oid)] = definition

    return ParseResult(
        definitions=tuple(sorted(unique.values(), key=lambda item: (item.oid, item.module, item.name))),
        modules=tuple(sorted(generated)),
        warnings=tuple(warnings),
    )
