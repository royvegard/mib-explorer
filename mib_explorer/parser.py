"""PySMI-backed loading and normalization for local MIB/SMI files."""

from __future__ import annotations

import json
import textwrap
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
                files.add(candidate.resolve())
        elif path.is_file():
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


def _extract_integer_enumerations(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, dict):
        return ()

    syntax_type = str(value.get("type") or "").casefold()
    if syntax_type not in {"integer", "integer32"}:
        return ()

    enumerations = value.get("enumeration")
    if not isinstance(enumerations, dict):
        constraints = value.get("constraints")
        if isinstance(constraints, dict):
            enumerations = constraints.get("enumeration")
    if not isinstance(enumerations, dict):
        return ()

    return tuple(
        (str(name), number)
        for name, number in enumerations.items()
        if isinstance(number, int) and not isinstance(number, bool)
    )


def _find_raw_description(value: object) -> str | None:
    if isinstance(value, tuple):
        if len(value) == 2 and value[0] == "DESCRIPTION" and isinstance(value[1], str):
            return value[1]
        children = value[1:]
    elif isinstance(value, list):
        children = value
    else:
        return None

    for child in children:
        description = _find_raw_description(child)
        if description is not None:
            return description
    return None


def _normalize_description_indentation(description: str) -> str:
    """Remove the common source margin while retaining relative indentation."""
    first_line, separator, continuation = description.partition("\n")
    if not separator:
        return description
    return first_line + separator + textwrap.dedent(continuation)


def _extract_raw_descriptions(parsed_modules: Iterable[object]) -> dict[tuple[str, str], str]:
    descriptions: dict[tuple[str, str], str] = {}
    for parsed_module in parsed_modules:
        if not isinstance(parsed_module, tuple) or len(parsed_module) < 4:
            continue
        module_name, declarations = parsed_module[0], parsed_module[3]
        if not isinstance(module_name, str) or not isinstance(declarations, list):
            continue

        for declaration in declarations:
            if not isinstance(declaration, tuple) or len(declaration) < 2:
                continue
            symbol_name = declaration[1]
            if not isinstance(symbol_name, str):
                continue
            description = _find_raw_description(declaration)
            if description is not None:
                descriptions[(module_name, symbol_name)] = _normalize_description_indentation(
                    description
                )

    return descriptions


class _DescriptionPreservingParser:
    def __init__(self) -> None:
        self._parser = SmiStarParser()
        self.raw_descriptions: dict[tuple[str, str], str] = {}

    def parse(self, data: str, **kwargs):
        parsed_modules = self._parser.parse(data, **kwargs)
        self.raw_descriptions.update(_extract_raw_descriptions(parsed_modules))
        return parsed_modules


def _module_definitions(
    module: str,
    source: str,
    raw_descriptions: dict[tuple[str, str], str],
    source_files: dict[str, str],
    source_paths: dict[str, str],
) -> tuple[list[OidDefinition], list[str]]:
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

        name = str(entry.get("name") or symbol)
        description = raw_descriptions.get((module, name), str(entry.get("description") or ""))
        syntax = entry.get("syntax")
        definitions.append(
            OidDefinition(
                name=name,
                oid=oid,
                module=module,
                kind=str(entry.get("nodetype") or entry.get("class") or ""),
                syntax=_display_value(syntax),
                access=str(entry.get("maxaccess") or entry.get("access") or ""),
                status=_display_value(entry.get("status")),
                description=description,
                source_file=source_files.get(module, ""),
                source_path=source_paths.get(module, ""),
                integer_enumerations=_extract_integer_enumerations(syntax),
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

    description_parser = _DescriptionPreservingParser()
    compiler = MibCompiler(
        description_parser,
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

    source_files: dict[str, str] = {}
    source_paths: dict[str, str] = {}
    for module, status in statuses.items():
        source_file = getattr(status, "file", None)
        if source_file:
            source_files[module] = Path(str(source_file)).name

        source_uri = getattr(status, "path", None)
        if isinstance(source_uri, str) and source_uri:
            source_path = (
                source_uri.removeprefix("file://")
                if source_uri.startswith("file://")
                else source_uri
            )
            source_paths[module] = source_path
            source_files.setdefault(module, Path(source_path).name)

    definitions: list[OidDefinition] = []
    for module, document in sorted(generated.items()):
        module_records, module_warnings = _module_definitions(
            module,
            document,
            description_parser.raw_descriptions,
            source_files,
            source_paths,
        )
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
