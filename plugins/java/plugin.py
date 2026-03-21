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

CLASS_PATTERN = re.compile(
    r"\b(?:public|protected|private|abstract|final|static|\s)*"
    r"(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
METHOD_PATTERN = re.compile(
    r"(?m)^\s*(?:public|protected|private|static|final|abstract|synchronized|native|default|\s)+"
    r"(?:<[^>]+>\s+)?(?:[A-Za-z_][A-Za-z0-9_<>\[\].?]+\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:throws\s+[^{]+)?\{"
)


def _extract_java_nodes(root_path: str) -> list[ExtractedNode]:
    extracted_nodes: list[ExtractedNode] = []

    for file_path in iter_source_files(root_path, JavaLanguagePlugin.extensions):
        source = read_source_file(file_path)
        if not source:
            continue

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
                method_start = class_start + body_start + 1 + method_match.start()
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
                        summary=f"{JavaLanguagePlugin.label} method on {class_name}.",
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
                    summary=f"{JavaLanguagePlugin.label} {class_match.group(1)} with {len(method_names)} method(s).",
                )
            )

    return extracted_nodes


class JavaLanguagePlugin(BaseLanguagePlugin):
    language = "java"
    label = "Java"
    extensions = (".java",)
    ready = True

    def scan(self, root_path: str, **options: Any) -> dict[str, Any]:
        return build_graph_from_nodes(
            language=self.language,
            label=self.label,
            root_path=root_path,
            extracted_nodes=_extract_java_nodes(root_path),
        )


if __name__ == "__main__":
    print(JavaLanguagePlugin().describe())
