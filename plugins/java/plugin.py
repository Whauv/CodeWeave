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

PACKAGE_PATTERN = re.compile(r"(?m)^\s*package\s+([A-Za-z0-9_.]+)\s*;")
IMPORT_PATTERN = re.compile(r"(?m)^\s*import\s+([A-Za-z0-9_.*]+)\s*;")
CLASS_PATTERN = re.compile(
    r"\b(?:public|protected|private|abstract|final|static|\s)*"
    r"(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+extends\s+([A-Za-z_][A-Za-z0-9_]*))?"
    r"(?:\s+implements\s+([^{]+))?"
)
METHOD_PATTERN = re.compile(
    r"(?m)^\s*(?:public|protected|private|static|final|abstract|synchronized|native|default|\s)+"
    r"(?:<[^>]+>\s+)?(?:[A-Za-z_][A-Za-z0-9_<>\[\].?]+\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:throws\s+[^{]+)?\{"
)


def _parse_import_aliases(source: str) -> tuple[dict[str, tuple[str | None, str]], list[str]]:
    aliases: dict[str, tuple[str | None, str]] = {}
    wildcard_packages: list[str] = []
    for import_path in IMPORT_PATTERN.findall(source):
        if import_path.endswith(".*"):
            wildcard_packages.append(import_path[:-2])
            continue
        class_name = import_path.split(".")[-1]
        aliases[normalize_symbol_name(class_name)] = (None, class_name)
    return aliases, wildcard_packages


def _extract_java_graph_inputs(
    root_path: str,
) -> tuple[list[ExtractedNode], list[ExtractedEdge], CallAliasMap]:
    extracted_nodes: list[ExtractedNode] = []
    explicit_edges: list[ExtractedEdge] = []
    call_aliases: CallAliasMap = {}

    file_packages: dict[str, str] = {}
    package_symbols: dict[str, list[tuple[str, str]]] = {}
    file_import_aliases: dict[str, dict[str, tuple[str | None, str]]] = {}
    file_wildcards: dict[str, list[str]] = {}
    file_node_names: dict[str, list[str]] = {}

    for file_path in iter_source_files(root_path, JavaLanguagePlugin.extensions):
        resolved_file = str(file_path.resolve())
        source = read_source_file(file_path)
        if not source:
            continue

        package_match = PACKAGE_PATTERN.search(source)
        package_name = package_match.group(1) if package_match else file_path.parent.name
        file_packages[resolved_file] = package_name
        import_aliases, wildcard_packages = _parse_import_aliases(source)
        file_import_aliases[resolved_file] = import_aliases
        file_wildcards[resolved_file] = wildcard_packages
        local_node_names: list[str] = []

        for class_match in CLASS_PATTERN.finditer(source):
            class_name = class_match.group(2)
            class_source = extract_braced_block(source, class_match.start())
            if not class_source:
                continue

            class_start = class_match.start()
            body_start = class_source.find("{")
            body_end = class_source.rfind("}")
            class_body = class_source[body_start + 1 : body_end] if body_start != -1 and body_end != -1 else ""
            method_names: list[str] = []

            for method_match in METHOD_PATTERN.finditer(class_body):
                method_name = method_match.group(1)
                method_names.append(method_name)
                qualified_name = f"{class_name}.{method_name}"
                local_node_names.append(qualified_name)
                method_start = class_start + body_start + 1 + method_match.start()
                method_source = extract_braced_block(source, method_start)
                extracted_nodes.append(
                    ExtractedNode(
                        name=qualified_name,
                        file=resolved_file,
                        line=line_number_at(source, method_start),
                        source_code=method_source,
                        node_type="function",
                        args=clean_args(method_match.group(2)),
                        aliases=(method_name,),
                        summary=f"{JavaLanguagePlugin.label} method on {class_name}.",
                    )
                )

            local_node_names.append(class_name)
            extracted_nodes.append(
                ExtractedNode(
                    name=class_name,
                    file=resolved_file,
                    line=line_number_at(source, class_start),
                    source_code=class_source,
                    node_type="class",
                    args=[],
                    methods=tuple(method_names),
                    summary=f"{JavaLanguagePlugin.label} {class_match.group(1)} with {len(method_names)} method(s).",
                )
            )
            package_symbols.setdefault(package_name, []).append((resolved_file, class_name))

            extends_name = (class_match.group(3) or "").strip()
            if extends_name:
                target_file, target_symbol = import_aliases.get(normalize_symbol_name(extends_name), (None, extends_name))
                explicit_edges.append(
                    ExtractedEdge(
                        source_name=class_name,
                        source_file=resolved_file,
                        target_name=target_symbol,
                        target_file=target_file,
                        edge_type="extends",
                    )
                )

            implements_block = (class_match.group(4) or "").strip()
            if implements_block:
                for interface_name in [value.strip() for value in implements_block.split(",") if value.strip()]:
                    target_file, target_symbol = import_aliases.get(normalize_symbol_name(interface_name), (None, interface_name))
                    explicit_edges.append(
                        ExtractedEdge(
                            source_name=class_name,
                            source_file=resolved_file,
                            target_name=target_symbol,
                            target_file=target_file,
                            edge_type="implements",
                        )
                    )

        file_node_names[resolved_file] = local_node_names

    for file_path, node_names in file_node_names.items():
        import_aliases = file_import_aliases.get(file_path, {})
        wildcard_packages = file_wildcards.get(file_path, [])
        for node_name in node_names:
            caller_key = (file_path, node_name)
            alias_map = call_aliases.setdefault(caller_key, {})
            for alias_name, (target_file, target_symbol) in import_aliases.items():
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
            for package_name in wildcard_packages:
                for target_file, target_symbol in package_symbols.get(package_name, []):
                    alias_map.setdefault(normalize_symbol_name(target_symbol), []).append((target_file, target_symbol))
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


class JavaLanguagePlugin(BaseLanguagePlugin):
    language = "java"
    label = "Java"
    extensions = (".java",)
    ready = True

    def scan(self, root_path: str, **options: Any) -> dict[str, Any]:
        extracted_nodes, explicit_edges, call_aliases = _extract_java_graph_inputs(root_path)
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
    print(JavaLanguagePlugin().describe())
