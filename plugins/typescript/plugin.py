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

CLASS_PATTERN = re.compile(
    r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+extends\s+([A-Za-z_][A-Za-z0-9_]*))?(?:\s+implements\s+([^{]+))?"
)
INTERFACE_PATTERN = re.compile(
    r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+extends\s+([^{]+))?"
)
FUNCTION_PATTERNS = (
    re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
        re.MULTILINE,
    ),
    re.compile(
        r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>",
        re.MULTILINE,
    ),
)
METHOD_PATTERN = re.compile(
    r"(?m)^\s*(?:public|private|protected|static|async|override|readonly|get|set|\s)*"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*\{"
)
IMPORT_PATTERN = re.compile(r"import\s+(.+?)\s+from\s+[\"']([^\"']+)[\"']", re.MULTILINE)
RE_EXPORT_PATTERN = re.compile(r"export\s+\{([^}]+)\}\s+from\s+[\"']([^\"']+)[\"']", re.MULTILINE)


def _resolve_typescript_import(source_file: Path, import_path: str) -> Path | None:
    if not import_path.startswith("."):
        return None

    candidate_root = (source_file.parent / import_path).resolve()
    extension_candidates = [
        candidate_root,
        candidate_root.with_suffix(".ts"),
        candidate_root.with_suffix(".tsx"),
        candidate_root.with_suffix(".js"),
        candidate_root.with_suffix(".jsx"),
        candidate_root / "index.ts",
        candidate_root / "index.tsx",
        candidate_root / "index.js",
        candidate_root / "index.jsx",
    ]
    for candidate in extension_candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _parse_import_spec(spec: str) -> tuple[str | None, dict[str, str], str | None]:
    default_import: str | None = None
    named_imports: dict[str, str] = {}
    namespace_import: str | None = None
    cleaned = spec.strip()
    if not cleaned:
        return default_import, named_imports, namespace_import

    if cleaned.startswith("* as "):
        namespace_import = cleaned[5:].strip()
        return default_import, named_imports, namespace_import

    brace_match = re.search(r"\{([^}]*)\}", cleaned)
    if brace_match:
        named_section = brace_match.group(1)
        prefix = cleaned[: brace_match.start()].strip().rstrip(",").strip()
        if prefix:
            default_import = prefix
        for part in named_section.split(","):
            token = part.strip()
            if not token:
                continue
            if " as " in token:
                imported_name, alias_name = [value.strip() for value in token.split(" as ", 1)]
            else:
                imported_name = token
                alias_name = token
            named_imports[normalize_symbol_name(alias_name)] = imported_name
        return default_import, named_imports, namespace_import

    default_import = cleaned.split(",", 1)[0].strip()
    return default_import or None, named_imports, namespace_import


def _extract_typescript_graph_inputs(
    root_path: str,
) -> tuple[list[ExtractedNode], list[ExtractedEdge], CallAliasMap]:
    extracted_nodes: list[ExtractedNode] = []
    explicit_edges: list[ExtractedEdge] = []
    call_aliases: CallAliasMap = {}

    file_nodes: dict[str, list[str]] = {}
    file_import_aliases: dict[str, dict[str, tuple[str, str | None]]] = {}

    source_files = iter_source_files(root_path, TypeScriptLanguagePlugin.extensions)
    for file_path in source_files:
        resolved_file = str(file_path.resolve())
        source = read_source_file(file_path)
        if not source:
            continue

        class_ranges: list[tuple[int, int]] = []
        local_node_names: list[str] = []

        import_aliases: dict[str, tuple[str, str | None]] = {}
        for import_match in IMPORT_PATTERN.finditer(source):
            spec = import_match.group(1)
            import_path = import_match.group(2)
            target_file = _resolve_typescript_import(file_path, import_path)
            if target_file is None:
                continue
            default_import, named_imports, namespace_import = _parse_import_spec(spec)
            target_file_str = str(target_file)
            if default_import:
                import_aliases[normalize_symbol_name(default_import)] = (target_file_str, default_import)
            for alias_name, imported_name in named_imports.items():
                import_aliases[alias_name] = (target_file_str, imported_name)
            if namespace_import:
                import_aliases[normalize_symbol_name(namespace_import)] = (target_file_str, None)

        for export_match in RE_EXPORT_PATTERN.finditer(source):
            import_path = export_match.group(2)
            target_file = _resolve_typescript_import(file_path, import_path)
            if target_file is None:
                continue
            for part in export_match.group(1).split(","):
                token = part.strip()
                if not token:
                    continue
                if " as " in token:
                    imported_name, alias_name = [value.strip() for value in token.split(" as ", 1)]
                else:
                    imported_name = token
                    alias_name = token
                import_aliases[normalize_symbol_name(alias_name)] = (str(target_file), imported_name)

        file_import_aliases[resolved_file] = import_aliases

        for match in CLASS_PATTERN.finditer(source):
            class_name = match.group(1)
            class_source = extract_braced_block(source, match.start())
            if not class_source:
                continue

            class_start = match.start()
            class_end = class_start + len(class_source)
            class_ranges.append((class_start, class_end))
            method_names: list[str] = []
            body_start = class_source.find("{")
            body_end = class_source.rfind("}")
            class_body = class_source[body_start + 1 : body_end] if body_start != -1 and body_end != -1 else ""

            for method_match in METHOD_PATTERN.finditer(class_body):
                method_name = method_match.group(1)
                if method_name == "constructor":
                    continue
                qualified_name = f"{class_name}.{method_name}"
                method_names.append(method_name)
                local_node_names.append(qualified_name)
                local_offset = body_start + 1 + method_match.start()
                method_start = class_start + local_offset
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
                        summary=f"{TypeScriptLanguagePlugin.label} method on {class_name}.",
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
                    summary=f"{TypeScriptLanguagePlugin.label} class with {len(method_names)} method(s).",
                )
            )

            extends_name = (match.group(2) or "").strip()
            if extends_name:
                target_file, target_symbol = import_aliases.get(normalize_symbol_name(extends_name), (None, extends_name))
                explicit_edges.append(
                    ExtractedEdge(
                        source_name=class_name,
                        source_file=resolved_file,
                        target_name=target_symbol or extends_name,
                        target_file=target_file,
                        edge_type="extends",
                    )
                )
            implements_block = (match.group(3) or "").strip()
            if implements_block:
                for interface_name in [value.strip() for value in implements_block.split(",") if value.strip()]:
                    target_file, target_symbol = import_aliases.get(normalize_symbol_name(interface_name), (None, interface_name))
                    explicit_edges.append(
                        ExtractedEdge(
                            source_name=class_name,
                            source_file=resolved_file,
                            target_name=target_symbol or interface_name,
                            target_file=target_file,
                            edge_type="implements",
                        )
                    )

        for interface_match in INTERFACE_PATTERN.finditer(source):
            interface_name = interface_match.group(1)
            interface_source = extract_braced_block(source, interface_match.start())
            if not interface_source:
                continue
            interface_start = interface_match.start()
            local_node_names.append(interface_name)
            extracted_nodes.append(
                ExtractedNode(
                    name=interface_name,
                    file=resolved_file,
                    line=line_number_at(source, interface_start),
                    source_code=interface_source,
                    node_type="class",
                    args=[],
                    methods=(),
                    summary=f"{TypeScriptLanguagePlugin.label} interface declaration.",
                )
            )
            extends_block = (interface_match.group(2) or "").strip()
            if extends_block:
                for target_name in [value.strip() for value in extends_block.split(",") if value.strip()]:
                    target_file, target_symbol = import_aliases.get(normalize_symbol_name(target_name), (None, target_name))
                    explicit_edges.append(
                        ExtractedEdge(
                            source_name=interface_name,
                            source_file=resolved_file,
                            target_name=target_symbol or target_name,
                            target_file=target_file,
                            edge_type="extends",
                        )
                    )

        for pattern in FUNCTION_PATTERNS:
            for match in pattern.finditer(source):
                start = match.start()
                if any(range_start <= start < range_end for range_start, range_end in class_ranges):
                    continue
                function_name = match.group(1)
                function_source = extract_braced_block(source, start)
                local_node_names.append(function_name)
                extracted_nodes.append(
                    ExtractedNode(
                        name=function_name,
                        file=resolved_file,
                        line=line_number_at(source, start),
                        source_code=function_source,
                        node_type="function",
                        args=clean_args(match.group(2)),
                        summary=f"{TypeScriptLanguagePlugin.label} function in {Path(file_path).name}.",
                    )
                )

        file_nodes[resolved_file] = local_node_names

    for file_path, node_names in file_nodes.items():
        import_aliases = file_import_aliases.get(file_path, {})
        for node_name in node_names:
            caller_key = (file_path, node_name)
            alias_map = call_aliases.setdefault(caller_key, {})
            for alias_name, target in import_aliases.items():
                target_file, target_symbol = target
                alias_map.setdefault(alias_name, []).append((target_file, target_symbol))
                if target_symbol:
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


class TypeScriptLanguagePlugin(BaseLanguagePlugin):
    language = "typescript"
    label = "TypeScript"
    extensions = (".ts", ".tsx", ".js", ".jsx")
    ready = True

    def scan(self, root_path: str, **options: Any) -> dict[str, Any]:
        extracted_nodes, explicit_edges, call_aliases = _extract_typescript_graph_inputs(root_path)
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
    print(TypeScriptLanguagePlugin().describe())
