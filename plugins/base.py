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
    calls = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source_code))
    return {name for name in calls if name not in IGNORED_CALL_NAMES}


def build_graph_from_nodes(
    language: str,
    label: str,
    root_path: str,
    extracted_nodes: list[ExtractedNode],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    name_index: dict[str, set[str]] = {}

    for extracted in extracted_nodes:
        node_id = get_node_id(language, extracted.file, extracted.name)
        node_payload: dict[str, Any] = {
            "id": node_id,
            "name": extracted.name,
            "file": extracted.file,
            "line": extracted.line,
            "source_code": extracted.source_code,
            "type": extracted.node_type,
            "args": extracted.args,
            "summary": extracted.summary
            or f"{label} {extracted.node_type} defined in {Path(extracted.file).name}.",
            "mutation_status": "stable",
            "mutation_color": "#aaaaaa",
            "churn_count": 0,
            "last_modified_commit": None,
        }
        if extracted.methods:
            node_payload["methods"] = list(extracted.methods)
        nodes.append(node_payload)

        lookup_names = {extracted.name, *extracted.aliases}
        for lookup_name in lookup_names:
            if lookup_name:
                name_index.setdefault(lookup_name, set()).add(node_id)

    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()

    for node in nodes:
        for call_name in infer_call_names(node.get("source_code", "")):
            target_ids = name_index.get(call_name, set())
            for target_id in target_ids:
                if target_id == node["id"]:
                    continue
                edge = (node["id"], target_id)
                if edge in seen_edges:
                    continue
                seen_edges.add(edge)
                edges.append({"source": node["id"], "target": target_id})

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "language": language,
            "plugin_label": label,
            "mode": "static",
            "root_path": str(Path(root_path).resolve()),
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
        },
    }


if __name__ == "__main__":
    print("Base plugin helpers loaded.")
