from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx

DEFAULT_MAX_DEPTH = 2


def _color_for_depth(depth: int) -> str:
    if depth <= 1:
        return "#a855f7"
    if depth == 2:
        return "#c084fc"
    if depth == 3:
        return "#d8b4fe"
    return "#e9d5ff"


def _bfs_depths(graph: nx.DiGraph, start_id: str, max_depth: int) -> dict[str, int]:
    depth_map: dict[str, int] = {start_id: 0}
    queue: list[tuple[str, int]] = [(start_id, 0)]
    while queue:
        node_id, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for neighbor_id in graph.successors(node_id):
            next_depth = depth + 1
            previous_depth = depth_map.get(neighbor_id)
            if previous_depth is not None and previous_depth <= next_depth:
                continue
            depth_map[neighbor_id] = next_depth
            queue.append((neighbor_id, next_depth))
    return depth_map


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
    upstream_depths = _bfs_depths(reversed_graph, node_id, DEFAULT_MAX_DEPTH)
    downstream_depths = _bfs_depths(graph, node_id, DEFAULT_MAX_DEPTH)

    combined_depths: dict[str, int] = {}
    for candidate_id, depth in upstream_depths.items():
        combined_depths[candidate_id] = depth
    for candidate_id, depth in downstream_depths.items():
        previous_depth = combined_depths.get(candidate_id)
        if previous_depth is None or depth < previous_depth:
            combined_depths[candidate_id] = depth

    affected_nodes = sorted(
        combined_depths.keys(),
        key=lambda candidate_id: (
            combined_depths[candidate_id],
            str(graph.nodes[candidate_id].get("name") or candidate_id).lower(),
            candidate_id,
        ),
    )
    depth_map = {candidate_id: combined_depths[candidate_id] for candidate_id in affected_nodes}
    risk_colors = {candidate_id: _color_for_depth(depth_map[candidate_id]) for candidate_id in affected_nodes}

    affected_modules = {
        graph.nodes[affected_node_id].get("file", "")
        for affected_node_id in affected_nodes
        if graph.nodes[affected_node_id].get("file")
    }
    epicenter_name = graph.nodes[node_id].get("name", node_id)
    impacted_count = max(len(affected_nodes) - 1, 0)
    upstream_count = max(len(upstream_depths) - 1, 0)
    downstream_count = max(len(downstream_depths) - 1, 0)

    return {
        "epicenter": node_id,
        "epicenter_name": epicenter_name,
        "affected_nodes": affected_nodes,
        "depth_map": depth_map,
        "risk_colors": risk_colors,
        "upstream_affected_count": upstream_count,
        "downstream_affected_count": downstream_count,
        "max_depth": DEFAULT_MAX_DEPTH,
        "summary": (
            f"Deleting {epicenter_name} impacts {impacted_count} nodes "
            f"(upstream: {upstream_count}, downstream: {downstream_count}) "
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
