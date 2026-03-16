from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx


def _color_for_depth(depth: int) -> str:
    if depth <= 1:
        return "#ff2222"
    if depth == 2:
        return "#ff6644"
    if depth == 3:
        return "#ff9966"
    return "#ffccaa"


def compute_blast_radius(graph_data: dict[str, Any], node_id: str) -> dict[str, Any]:
    graph = nx.DiGraph()

    for node in graph_data.get("nodes", []):
        graph.add_node(node["id"], **node)

    for edge in graph_data.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        if source and target:
            graph.add_edge(source, target)

    if node_id not in graph:
        return {
            "epicenter": node_id,
            "epicenter_name": "",
            "affected_nodes": [],
            "depth_map": {},
            "risk_colors": {},
            "summary": "Node not found.",
        }

    reversed_graph = graph.reverse(copy=True)
    layers = list(nx.bfs_layers(reversed_graph, [node_id]))
    depth_map: dict[str, int] = {}
    affected_nodes: list[str] = []
    risk_colors: dict[str, str] = {}

    for depth, layer in enumerate(layers):
        for affected_node_id in layer:
            depth_map[affected_node_id] = depth
            affected_nodes.append(affected_node_id)
            risk_colors[affected_node_id] = _color_for_depth(depth)

    affected_modules = {
        graph.nodes[affected_node_id].get("file", "")
        for affected_node_id in affected_nodes
        if graph.nodes[affected_node_id].get("file")
    }
    epicenter_name = graph.nodes[node_id].get("name", node_id)
    impacted_count = max(len(affected_nodes) - 1, 0)

    return {
        "epicenter": node_id,
        "epicenter_name": epicenter_name,
        "affected_nodes": affected_nodes,
        "depth_map": depth_map,
        "risk_colors": risk_colors,
        "summary": (
            f"Changing {epicenter_name} affects {impacted_count} functions "
            f"across {len(affected_modules)} modules"
        ),
    }


if __name__ == "__main__":
    sample_graph = {
        "nodes": [
            {"id": "a", "name": "alpha", "file": str(Path("a.py")), "line": 1},
            {"id": "b", "name": "beta", "file": str(Path("b.py")), "line": 2},
        ],
        "edges": [{"source": "b", "target": "a"}],
    }
    print(compute_blast_radius(sample_graph, "a"))
