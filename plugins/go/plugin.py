from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from plugins.base import (
    BaseLanguagePlugin,
    ExtractedNode,
    build_graph_from_nodes,
    clean_args,
    extract_braced_block,
    iter_source_files,
    line_number_at,
    read_source_file,
)

TYPE_PATTERN = re.compile(r"\btype\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\b")
FUNCTION_PATTERN = re.compile(
    r"func\s*(\(\s*[^)]*?\))?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
    re.MULTILINE,
)
RECEIVER_TYPE_PATTERN = re.compile(r"\*\s*([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*)")


def _extract_receiver_type(receiver: str) -> str:
    for match in RECEIVER_TYPE_PATTERN.finditer(receiver or ""):
        receiver_type = match.group(1) or match.group(2)
        if receiver_type and receiver_type not in {"func"}:
            return receiver_type
    return ""


def _extract_go_nodes(root_path: str) -> list[ExtractedNode]:
    extracted_nodes: list[ExtractedNode] = []

    for file_path in iter_source_files(root_path, GoLanguagePlugin.extensions):
        source = read_source_file(file_path)
        if not source:
            continue

        type_names: dict[str, list[str]] = {}
        file_nodes: list[ExtractedNode] = []
        for type_match in TYPE_PATTERN.finditer(source):
            type_name = type_match.group(1)
            type_source = extract_braced_block(source, type_match.start())
            type_names.setdefault(type_name, [])
            file_nodes.append(
                ExtractedNode(
                    name=type_name,
                    file=str(file_path.resolve()),
                    line=line_number_at(source, type_match.start()),
                    source_code=type_source,
                    node_type="class",
                    args=[],
                    summary=f"{GoLanguagePlugin.label} {type_match.group(2)} declaration.",
                )
            )

        for function_match in FUNCTION_PATTERN.finditer(source):
            receiver = function_match.group(1) or ""
            function_name = function_match.group(2)
            args = clean_args(function_match.group(3))
            receiver_type = _extract_receiver_type(receiver)
            qualified_name = f"{receiver_type}.{function_name}" if receiver_type else function_name
            function_source = extract_braced_block(source, function_match.start())

            if receiver_type:
                type_names.setdefault(receiver_type, []).append(function_name)

            file_nodes.append(
                ExtractedNode(
                    name=qualified_name,
                    file=str(file_path.resolve()),
                    line=line_number_at(source, function_match.start()),
                    source_code=function_source,
                    node_type="function",
                    args=args,
                    aliases=(function_name,),
                    summary=f"{GoLanguagePlugin.label} {'method' if receiver_type else 'function'} in {Path(file_path).name}.",
                )
            )

        for node in file_nodes:
            if node.node_type != "class":
                extracted_nodes.append(node)
                continue
            methods = tuple(type_names.get(node.name, []))
            if methods:
                extracted_nodes.append(
                    ExtractedNode(
                        name=node.name,
                        file=node.file,
                        line=node.line,
                        source_code=node.source_code,
                        node_type=node.node_type,
                        args=node.args,
                        aliases=node.aliases,
                        methods=methods,
                        summary=f"{GoLanguagePlugin.label} type with {len(methods)} method(s).",
                    )
                )
            else:
                extracted_nodes.append(
                    ExtractedNode(
                        name=node.name,
                        file=node.file,
                        line=node.line,
                        source_code=node.source_code,
                        node_type=node.node_type,
                        args=node.args,
                        aliases=node.aliases,
                        methods=node.methods,
                        summary=node.summary,
                    )
                )

    return extracted_nodes


class GoLanguagePlugin(BaseLanguagePlugin):
    language = "go"
    label = "Go"
    extensions = (".go",)
    ready = True

    def scan(self, root_path: str, **options: Any) -> dict[str, Any]:
        return build_graph_from_nodes(
            language=self.language,
            label=self.label,
            root_path=root_path,
            extracted_nodes=_extract_go_nodes(root_path),
        )


if __name__ == "__main__":
    print(GoLanguagePlugin().describe())
