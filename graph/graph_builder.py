from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser import ast_extractor, summarizer


def build_graph(root_path: str) -> dict[str, list[dict[str, Any]]]:
    graph_data = ast_extractor.extract(root_path)
    for node in graph_data.get("nodes", []):
        if node.get("type") == "function":
            node["summary"] = summarizer.summarize_node(
                node.get("source_code", ""),
                node.get("id", ""),
            )
        else:
            node["summary"] = "Class definition and methods."
    return graph_data


if __name__ == "__main__":
    current_directory = str(Path.cwd())
    graph = build_graph(current_directory)
    print(f"Built graph with {len(graph['nodes'])} nodes and {len(graph['edges'])} edges")
