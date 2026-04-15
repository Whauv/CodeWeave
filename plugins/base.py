from __future__ import annotations

import hashlib
import keyword
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    "out",
    ".next",
    ".nuxt",
    "coverage",
    "vendor",
}
IGNORED_CALL_NAMES = set(keyword.kwlist) | {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "new",
    "super",
    "this",
}
EDGE_STYLE_BY_TYPE = {
    "call": {"label": "Calls", "color": "#95c1d6"},
    "import": {"label": "Imports", "color": "#7cc4fa"},
    "extends": {"label": "Extends", "color": "#f5c85b"},
    "implements": {"label": "Implements", "color": "#66d7d1"},
    "embeds": {"label": "Embeds", "color": "#af8bff"},
}


@dataclass(frozen=True)
class PluginDescriptor:
    language: str
    label: str
    extensions: tuple[str, ...]
    supports_mutation_tracking: bool
    ready: bool


class BaseLanguagePlugin(ABC):
    language: str
    label: str
    extensions: tuple[str, ...]
    supports_mutation_tracking: bool = False
    ready: bool = False

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            language=self.language,
            label=self.label,
            extensions=self.extensions,
            supports_mutation_tracking=self.supports_mutation_tracking,
            ready=self.ready,
        )

    @abstractmethod
    def scan(self, root_path: str, **options: Any) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class ExtractedNode:
    name: str
    file: str
    line: int
    source_code: str
    node_type: str
    args: list[str]
    aliases: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class ExtractedEdge:
    source_name: str
    edge_type: str
    target_name: str
    source_file: str | None = None
    target_file: str | None = None


CallAliasMap = dict[tuple[str, str], dict[str, list[tuple[str, str | None]]]]


def iter_source_files(root_path: str, extensions: tuple[str, ...]) -> list[Path]:
    root = Path(root_path).resolve()
    files: list[Path] = []
    lowered_extensions = tuple(ext.lower() for ext in extensions)

    for file_path in root.rglob("*"):
        if any(part in SKIP_DIRECTORIES for part in file_path.parts):
            continue
        if not file_path.is_file() or file_path.suffix.lower() not in lowered_extensions:
            continue
        files.append(file_path)

    return files


def read_source_file(file_path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            LOGGER.warning("Unable to read %s: %s", file_path, exc)
            return ""
    LOGGER.warning("Unable to decode %s", file_path)
    return ""


def get_node_id(language: str, file_path: str, symbol_name: str) -> str:
    payload = f"{language}::{Path(file_path).resolve()}::{symbol_name}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def normalize_symbol_name(name: str) -> str:
    return str(name or "").strip().lower()


def line_number_at(source: str, index: int) -> int:
    return source[:index].count("\n") + 1


def clean_args(raw_args: str) -> list[str]:
    args: list[str] = []
    for raw_part in (raw_args or "").split(","):
        part = raw_part.strip()
        if not part:
            continue
        part = part.split("=")[0].strip()
        part = part.split(":")[0].strip()
        part = part.replace("...", "").strip()
        tokens = [token for token in re.split(r"[\s*&]+", part) if token]
        if tokens:
            args.append(tokens[-1])
    return args


def extract_braced_block(source: str, start_index: int) -> str:
    brace_index = source.find("{", start_index)
    if brace_index == -1:
        return source[start_index:].split(";", 1)[0].strip()

    depth = 0
    for index in range(brace_index, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start_index : index + 1].strip()
    return source[start_index:].strip()


def infer_call_names(source_code: str) -> set[str]:
    direct_calls = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source_code))
    member_calls = {match[1] for match in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", source_code)}
    calls = direct_calls | member_calls
    return {name for name in calls if name not in IGNORED_CALL_NAMES}


def build_module_index(extracted_nodes: list[ExtractedNode]) -> dict[str, dict[str, set[str]]]:
    module_index: dict[str, dict[str, set[str]]] = {}
    for node in extracted_nodes:
        file_path = str(Path(node.file).resolve())
        file_index = module_index.setdefault(file_path, {})
        lookup_names = {normalize_symbol_name(node.name), *(normalize_symbol_name(alias) for alias in node.aliases)}
        for lookup_name in lookup_names:
            if lookup_name:
                file_index.setdefault(lookup_name, set()).add(node.name)
    return module_index


def resolve_module_target_names(
    module_index: dict[str, dict[str, set[str]]],
    target_file: str | None,
    target_name: str,
) -> list[tuple[str, str]]:
    normalized_target = normalize_symbol_name(target_name)
    if not normalized_target:
        return []

    if target_file:
        resolved_file = str(Path(target_file).resolve())
        names = module_index.get(resolved_file, {}).get(normalized_target, set())
        if names:
            return [(resolved_file, name) for name in sorted(names)]
        return []

    matches: list[tuple[str, str]] = []
    for file_path, name_index in module_index.items():
        for name in sorted(name_index.get(normalized_target, set())):
            matches.append((file_path, name))
    return matches


def edge_style(edge_type: str) -> dict[str, str]:
    return EDGE_STYLE_BY_TYPE.get(edge_type, {"label": edge_type.title(), "color": "#95c1d6"})


def _resolve_node_ids_for_name(
    name_index: dict[str, set[str]],
    file_name_index: dict[tuple[str, str], set[str]],
    file_path: str,
    symbol_name: str,
    explicit_targets: list[tuple[str, str | None]] | None = None,
) -> set[str]:
    resolved_ids: set[str] = set()
    if explicit_targets:
        for target_file, target_name in explicit_targets:
            normalized_name = normalize_symbol_name(target_name or "")
            if not normalized_name:
                continue
            if target_file:
                resolved_ids.update(file_name_index.get((str(Path(target_file).resolve()), normalized_name), set()))
            else:
                resolved_ids.update(name_index.get(normalized_name, set()))
        return resolved_ids

    normalized_symbol = normalize_symbol_name(symbol_name)
    resolved_ids.update(file_name_index.get((file_path, normalized_symbol), set()))
    if not resolved_ids:
        resolved_ids.update(name_index.get(normalized_symbol, set()))
    return resolved_ids


def build_graph_from_nodes(
    language: str,
    label: str,
    root_path: str,
    extracted_nodes: list[ExtractedNode],
    explicit_edges: list[ExtractedEdge] | None = None,
    call_aliases: CallAliasMap | None = None,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    name_index: dict[str, set[str]] = {}
    file_name_index: dict[tuple[str, str], set[str]] = {}
    node_id_by_symbol: dict[tuple[str, str], str] = {}

    for extracted in extracted_nodes:
        resolved_file = str(Path(extracted.file).resolve())
        node_id = get_node_id(language, resolved_file, extracted.name)
        node_payload: dict[str, Any] = {
            "id": node_id,
            "name": extracted.name,
            "file": resolved_file,
            "line": extracted.line,
            "source_code": extracted.source_code,
            "type": extracted.node_type,
            "args": extracted.args,
            "summary": extracted.summary
            or f"{label} {extracted.node_type} defined in {Path(resolved_file).name}.",
            "mutation_status": "stable",
            "mutation_color": "#aaaaaa",
            "churn_count": 0,
            "last_modified_commit": None,
        }
        if extracted.methods:
            node_payload["methods"] = list(extracted.methods)
        nodes.append(node_payload)

        node_id_by_symbol[(resolved_file, extracted.name)] = node_id
        lookup_names = {extracted.name, *extracted.aliases}
        for lookup_name in lookup_names:
            normalized_lookup = normalize_symbol_name(lookup_name)
            if not normalized_lookup:
                continue
            name_index.setdefault(normalized_lookup, set()).add(node_id)
            file_name_index.setdefault((resolved_file, normalized_lookup), set()).add(node_id)

    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    for node in nodes:
        resolved_file = str(Path(node.get("file") or "").resolve())
        caller_key = (resolved_file, str(node.get("name") or ""))
        alias_map = (call_aliases or {}).get(caller_key, {})
        for call_name in infer_call_names(node.get("source_code", "")):
            target_ids = _resolve_node_ids_for_name(
                name_index,
                file_name_index,
                resolved_file,
                call_name,
                alias_map.get(normalize_symbol_name(call_name)),
            )
            for target_id in target_ids:
                if target_id == node["id"]:
                    continue
                edge_key = (node["id"], target_id, "call")
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                style = edge_style("call")
                edges.append(
                    {
                        "source": node["id"],
                        "target": target_id,
                        "type": "call",
                        "label": style["label"],
                        "color": style["color"],
                    }
                )

    for explicit_edge in explicit_edges or []:
        source_file = str(Path(explicit_edge.source_file).resolve()) if explicit_edge.source_file else ""
        source_id = node_id_by_symbol.get((source_file, explicit_edge.source_name))
        if not source_id:
            continue

        target_matches = resolve_module_target_names(
            build_module_index(extracted_nodes),
            explicit_edge.target_file,
            explicit_edge.target_name,
        )
        if not target_matches and explicit_edge.target_file:
            target_matches = [(str(Path(explicit_edge.target_file).resolve()), explicit_edge.target_name)]

        for target_file, target_name in target_matches:
            target_ids = file_name_index.get((str(Path(target_file).resolve()), normalize_symbol_name(target_name)), set())
            if not target_ids:
                target_ids = name_index.get(normalize_symbol_name(target_name), set())
            for target_id in target_ids:
                if target_id == source_id:
                    continue
                edge_key = (source_id, target_id, explicit_edge.edge_type)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                style = edge_style(explicit_edge.edge_type)
                edges.append(
                    {
                        "source": source_id,
                        "target": target_id,
                        "type": explicit_edge.edge_type,
                        "label": style["label"],
                        "color": style["color"],
                    }
                )

    edge_breakdown: dict[str, int] = {}
    for edge in edges:
        edge_type = str(edge.get("type") or "call")
        edge_breakdown[edge_type] = edge_breakdown.get(edge_type, 0) + 1

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "language": language,
            "plugin_label": label,
            "mode": "static",
            "root_path": str(Path(root_path).resolve()),
            "edge_breakdown": edge_breakdown,
        },
    }


def build_stub_graph(
    root_path: str,
    language: str,
    label: str,
    extensions: tuple[str, ...],
) -> dict[str, Any]:
    root = Path(root_path)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    for extension in extensions:
        for file_path in root.rglob(f"*{extension}"):
            if not file_path.is_file():
                continue
            file_id = hashlib.md5(f"{language}::{file_path.resolve()}".encode("utf-8")).hexdigest()
            nodes.append(
                {
                    "id": file_id,
                    "name": file_path.stem,
                    "file": str(file_path.resolve()),
                    "line": 1,
                    "source_code": "",
                    "type": "file",
                    "args": [],
                    "summary": f"{label} plugin stub detected this source file. Rich AST mapping is not implemented yet.",
                    "mutation_status": "stable",
                    "mutation_color": "#aaaaaa",
                    "churn_count": 0,
                    "last_modified_commit": None,
                }
            )

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "language": language,
            "plugin_label": label,
            "mode": "stub",
            "edge_breakdown": {},
        },
    }


if __name__ == "__main__":
    print("Base plugin helpers loaded.")
