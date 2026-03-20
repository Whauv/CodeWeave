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

CLASS_PATTERN = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)")
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


def _extract_typescript_nodes(root_path: str) -> list[ExtractedNode]:
    extracted_nodes: list[ExtractedNode] = []

    for file_path in iter_source_files(root_path, TypeScriptLanguagePlugin.extensions):
        source = read_source_file(file_path)
        if not source:
            continue

        class_ranges: list[tuple[int, int]] = []
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
                method_names.append(method_name)
                local_offset = body_start + 1 + method_match.start()
                method_start = class_start + local_offset
                method_source = extract_braced_block(source, method_start)
                extracted_nodes.append(
                    ExtractedNode(
                        name=f"{class_name}.{method_name}",
                        file=str(file_path.resolve()),
                        line=line_number_at(source, method_start),
                        source_code=method_source,
                        node_type="function",
                        args=clean_args(method_match.group(2)),
                        aliases=(method_name,),
                        summary=f"{TypeScriptLanguagePlugin.label} method on {class_name}.",
                    )
                )

            extracted_nodes.append(
                ExtractedNode(
                    name=class_name,
                    file=str(file_path.resolve()),
                    line=line_number_at(source, class_start),
                    source_code=class_source,
                    node_type="class",
                    args=[],
                    methods=tuple(method_names),
                    summary=f"{TypeScriptLanguagePlugin.label} class with {len(method_names)} method(s).",
                )
            )

        for pattern in FUNCTION_PATTERNS:
            for match in pattern.finditer(source):
                start = match.start()
                if any(range_start <= start < range_end for range_start, range_end in class_ranges):
                    continue
                function_source = extract_braced_block(source, start)
                extracted_nodes.append(
                    ExtractedNode(
                        name=match.group(1),
                        file=str(file_path.resolve()),
                        line=line_number_at(source, start),
                        source_code=function_source,
                        node_type="function",
                        args=clean_args(match.group(2)),
                        summary=f"{TypeScriptLanguagePlugin.label} function in {Path(file_path).name}.",
                    )
                )

    return extracted_nodes


class TypeScriptLanguagePlugin(BaseLanguagePlugin):
    language = "typescript"
    label = "TypeScript"
    extensions = (".ts", ".tsx", ".js", ".jsx")
    ready = True

    def scan(self, root_path: str) -> dict[str, Any]:
        return build_graph_from_nodes(
            language=self.language,
            label=self.label,
            root_path=root_path,
            extracted_nodes=_extract_typescript_nodes(root_path),
        )


if __name__ == "__main__":
    print(TypeScriptLanguagePlugin().describe())
