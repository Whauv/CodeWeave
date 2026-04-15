from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import networkx as nx


def _build_graph(graph_data: dict[str, Any]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for node in graph_data.get("nodes", []):
        graph.add_node(node.get("id"), **node)
    for edge in graph_data.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        if source and target:
            graph.add_edge(source, target, **edge)
    return graph


def _top_nodes(
    graph: nx.DiGraph,
    nodes_by_id: dict[str, dict[str, Any]],
    score_getter,
    limit: int = 6,
) -> list[dict[str, Any]]:
    ranked = sorted(
        (
            {
                "id": node_id,
                "name": nodes_by_id.get(node_id, {}).get("name"),
                "file": nodes_by_id.get(node_id, {}).get("file"),
                "score": score_getter(node_id),
            }
            for node_id in graph.nodes
        ),
        key=lambda item: (-int(item["score"]), str(item["name"] or "")),
    )
    return [item for item in ranked if item["score"]][:limit]


def _module_label(file_path: str) -> str:
    normalized = str(file_path or "").replace("\\", "/").strip()
    if not normalized:
        return "unknown"
    return Path(normalized).name or normalized


def compute_insights(graph_data: dict[str, Any]) -> dict[str, Any]:
    graph = _build_graph(graph_data)
    nodes_by_id = {str(node.get("id")): node for node in graph_data.get("nodes", []) if node.get("id")}

    fan_in = _top_nodes(graph, nodes_by_id, graph.in_degree)
    fan_out = _top_nodes(graph, nodes_by_id, graph.out_degree)

    orphan_nodes = [
        {
            "id": node_id,
            "name": nodes_by_id.get(node_id, {}).get("name"),
            "file": nodes_by_id.get(node_id, {}).get("file"),
        }
        for node_id in graph.nodes
        if graph.in_degree(node_id) == 0
    ]

    dead_code_candidates = [
        node
        for node in orphan_nodes
        if str(nodes_by_id.get(node["id"], {}).get("type") or "") == "function"
        and not str(nodes_by_id.get(node["id"], {}).get("name") or "").startswith("__")
    ]

    edge_type_breakdown = Counter(str(edge.get("type") or "call") for edge in graph_data.get("edges", []))

    coupled_modules_counter: Counter[tuple[str, str]] = Counter()
    module_graph = nx.DiGraph()
    for edge in graph_data.get("edges", []):
        source_node = nodes_by_id.get(str(edge.get("source")))
        target_node = nodes_by_id.get(str(edge.get("target")))
        source_file = str((source_node or {}).get("file") or "").strip()
        target_file = str((target_node or {}).get("file") or "").strip()
        if not source_file or not target_file or source_file == target_file:
            continue
        source_label = _module_label(source_file)
        target_label = _module_label(target_file)
        module_graph.add_edge(source_label, target_label)
        coupled_modules_counter[tuple(sorted((source_label, target_label)))] += 1

    strongly_connected_nodes = [
        sorted(component)
        for component in nx.strongly_connected_components(graph)
        if len(component) > 1
    ]
    strongly_connected_modules = [
        sorted(component)
        for component in nx.strongly_connected_components(module_graph)
        if len(component) > 1
    ]

    hot_modules = Counter()
    for node in graph_data.get("nodes", []):
        if str(node.get("mutation_status") or "").lower() == "hotspot":
            hot_modules[_module_label(str(node.get("file") or ""))] += 1

    summary_bits = [
        f"{len(graph_data.get('nodes', []))} nodes",
        f"{len(graph_data.get('edges', []))} typed edges",
        f"{len(strongly_connected_nodes)} node cycles",
        f"{len(dead_code_candidates)} dead-code candidates",
    ]

    return {
        "summary": "Architecture snapshot: " + ", ".join(summary_bits) + ".",
        "fan_in": fan_in,
        "fan_out": fan_out,
        "orphan_nodes": orphan_nodes[:12],
        "dead_code_candidates": dead_code_candidates[:12],
        "edge_type_breakdown": dict(edge_type_breakdown),
        "tight_coupling": [
            {"left": left, "right": right, "edges": count}
            for (left, right), count in coupled_modules_counter.most_common(6)
        ],
        "node_cycles": [
            [
                {
                    "id": node_id,
                    "name": nodes_by_id.get(node_id, {}).get("name"),
                    "file": nodes_by_id.get(node_id, {}).get("file"),
                }
                for node_id in component
            ]
            for component in strongly_connected_nodes[:6]
        ],
        "module_cycles": strongly_connected_modules[:6],
        "hot_modules": [{"module": module, "count": count} for module, count in hot_modules.most_common(6)],
    }


if __name__ == "__main__":
    sample_graph = {
        "nodes": [
            {"id": "a", "name": "a", "file": "a.py", "type": "function"},
            {"id": "b", "name": "b", "file": "b.py", "type": "function"},
        ],
        "edges": [{"source": "a", "target": "b", "type": "call"}],
    }
    print(compute_insights(sample_graph))
