from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from graph.insights import compute_insights
from plugins.base import (
    BaseLanguagePlugin,
    CallAliasMap,
    ExtractedEdge,
    ExtractedNode,
    build_graph_from_nodes,
    clean_args,
    extract_braced_block,
    iter_source_files,
    line_number_at,
    normalize_symbol_name,
    read_source_file,
)

PACKAGE_PATTERN = re.compile(r"(?m)^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")
TYPE_PATTERN = re.compile(r"\btype\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\b")
FUNCTION_PATTERN = re.compile(
    r"func\s*(\(\s*[^)]*?\))?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
    re.MULTILINE,
)
RECEIVER_TYPE_PATTERN = re.compile(r"\*\s*([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*)")
SINGLE_IMPORT_PATTERN = re.compile(r'(?m)^\s*import\s+(?:(\w+)\s+)?"([^"]+)"\s*$')
BLOCK_IMPORT_PATTERN = re.compile(r"import\s*\((.*?)\)", re.DOTALL)
BLOCK_IMPORT_ENTRY_PATTERN = re.compile(r'(?m)^\s*(?:(\w+)\s+)?"([^"]+)"\s*$')


def _extract_receiver_type(receiver: str) -> str:
    for match in RECEIVER_TYPE_PATTERN.finditer(receiver or ""):
        receiver_type = match.group(1) or match.group(2)
        if receiver_type and receiver_type not in {"func"}:
            return receiver_type
    return ""


def _extract_embedded_types(type_kind: str, type_source: str) -> list[str]:
    body_start = type_source.find("{")
    body_end = type_source.rfind("}")
    if body_start == -1 or body_end == -1 or body_end <= body_start:
        return []
    body = type_source[body_start + 1 : body_end]
    embedded_types: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.startswith("//"):
            continue
        if type_kind == "struct":
            if " " not in line and not line.startswith("*"):
                embedded_types.append(line.lstrip("*"))
        else:
            if "(" not in line and not line.startswith("~"):
                embedded_types.append(line.lstrip("*"))
    return embedded_types


def _parse_import_aliases(source: str, file_path: Path) -> dict[str, tuple[str, str | None]]:
    aliases: dict[str, tuple[str, str | None]] = {}
    for alias, import_path in SINGLE_IMPORT_PATTERN.findall(source):
        import_name = alias or Path(import_path).name
        aliases[normalize_symbol_name(import_name)] = (import_path, None)

    for block_match in BLOCK_IMPORT_PATTERN.finditer(source):
        for alias, import_path in BLOCK_IMPORT_ENTRY_PATTERN.findall(block_match.group(1)):
            import_name = alias or Path(import_path).name
            aliases[normalize_symbol_name(import_name)] = (import_path, None)

    return aliases


def _extract_go_graph_inputs(
    root_path: str,
) -> tuple[list[ExtractedNode], list[ExtractedEdge], CallAliasMap]:
    extracted_nodes: list[ExtractedNode] = []
    explicit_edges: list[ExtractedEdge] = []
    call_aliases: CallAliasMap = {}

    package_symbols: dict[str, list[tuple[str, str]]] = {}
    file_packages: dict[str, str] = {}
    file_import_aliases: dict[str, dict[str, tuple[str, str | None]]] = {}
    file_node_names: dict[str, list[str]] = {}

    source_files = iter_source_files(root_path, GoLanguagePlugin.extensions)
    for file_path in source_files:
        resolved_file = str(file_path.resolve())
        source = read_source_file(file_path)
        if not source:
            continue

        package_name_match = PACKAGE_PATTERN.search(source)
        package_name = package_name_match.group(1) if package_name_match else file_path.parent.name
        file_packages[resolved_file] = package_name
        file_import_aliases[resolved_file] = _parse_import_aliases(source, file_path)
        local_node_names: list[str] = []

        type_names: dict[str, list[str]] = {}
        file_nodes: list[ExtractedNode] = []
        for type_match in TYPE_PATTERN.finditer(source):
            type_name = type_match.group(1)
            type_kind = type_match.group(2)
            type_source = extract_braced_block(source, type_match.start())
            type_names.setdefault(type_name, [])
            local_node_names.append(type_name)
            file_nodes.append(
                ExtractedNode(
                    name=type_name,
                    file=resolved_file,
                    line=line_number_at(source, type_match.start()),
                    source_code=type_source,
                    node_type="class",
                    args=[],
                    summary=f"{GoLanguagePlugin.label} {type_kind} declaration.",
                )
            )
            package_symbols.setdefault(package_name, []).append((resolved_file, type_name))
            for embedded_type in _extract_embedded_types(type_kind, type_source):
                explicit_edges.append(
                    ExtractedEdge(
                        source_name=type_name,
                        source_file=resolved_file,
                        target_name=embedded_type,
                        edge_type="embeds",
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

            local_node_names.append(qualified_name)
            file_nodes.append(
                ExtractedNode(
                    name=qualified_name,
                    file=resolved_file,
                    line=line_number_at(source, function_match.start()),
                    source_code=function_source,
                    node_type="function",
                    args=args,
                    aliases=(function_name,),
                    summary=f"{GoLanguagePlugin.label} {'method' if receiver_type else 'function'} in {Path(file_path).name}.",
                )
            )
            package_symbols.setdefault(package_name, []).append((resolved_file, qualified_name))
            if not receiver_type:
                package_symbols.setdefault(package_name, []).append((resolved_file, function_name))

        for node in file_nodes:
            if node.node_type != "class":
                extracted_nodes.append(node)
                continue
            methods = tuple(type_names.get(node.name, []))
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
                    summary=f"{GoLanguagePlugin.label} type with {len(methods)} method(s)." if methods else node.summary,
                )
            )

        file_node_names[resolved_file] = local_node_names

    for file_path, node_names in file_node_names.items():
        aliases = file_import_aliases.get(file_path, {})
        for node_name in node_names:
            caller_key = (file_path, node_name)
            alias_map = call_aliases.setdefault(caller_key, {})
            for alias_name, (package_path, _) in aliases.items():
                target_package = Path(package_path).name
                target_symbols = package_symbols.get(target_package, [])
                for target_file, target_symbol in target_symbols:
                    alias_map.setdefault(alias_name, []).append((target_file, target_symbol))
                    explicit_edges.append(
                        ExtractedEdge(
                            source_name=node_name,
                            source_file=file_path,
                            target_name=target_symbol,
                            target_file=target_file,
                            edge_type="import",
                        )
                    )

    return extracted_nodes, explicit_edges, call_aliases


class GoLanguagePlugin(BaseLanguagePlugin):
    language = "go"
    label = "Go"
    extensions = (".go",)
    ready = True

    def scan(self, root_path: str, **options: Any) -> dict[str, Any]:
        extracted_nodes, explicit_edges, call_aliases = _extract_go_graph_inputs(root_path)
        graph_data = build_graph_from_nodes(
            language=self.language,
            label=self.label,
            root_path=root_path,
            extracted_nodes=extracted_nodes,
            explicit_edges=explicit_edges,
            call_aliases=call_aliases,
        )
        graph_data["insights"] = compute_insights(graph_data)
        return graph_data


if __name__ == "__main__":
    print(GoLanguagePlugin().describe())
