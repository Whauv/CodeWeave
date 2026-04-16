from __future__ import annotations

import os
import time
from collections import Counter
from typing import Any

try:
    from groq import Groq
except Exception:
    Groq = None

from graph import blast_radius

CHAT_RATE_LIMIT_UNTIL = 0.0
CHAT_RATE_LIMIT_COOLDOWN_SECONDS = 30


def _get_node_from_graph(graph_data: dict[str, Any] | None, node_id: str) -> dict[str, Any] | None:
    if graph_data is None:
        return None
    for node in graph_data.get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


def safe_join_names(values: list[str], limit: int = 12) -> str:
    if not values:
        return "None"
    if len(values) <= limit:
        return ", ".join(values)
    shown = ", ".join(values[:limit])
    return f"{shown}, and {len(values) - limit} more"


def format_file_label(file_path: str) -> str:
    normalized = str(file_path or "").replace("\\", "/").strip()
    if not normalized:
        return "unknown module"
    return normalized.split("/")[-1] or normalized


def build_node_index(graph_data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if graph_data is None:
        return {}
    return {
        str(node.get("id")): node
        for node in graph_data.get("nodes", [])
        if node.get("id") is not None
    }


def build_edge_pairs(graph_data: dict[str, Any] | None) -> list[tuple[str, str]]:
    if graph_data is None:
        return []
    pairs: list[tuple[str, str]] = []
    for edge in graph_data.get("edges", []):
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if source and target:
            pairs.append((source, target))
    return pairs


def collect_module_coupling(
    nodes_by_id: dict[str, dict[str, Any]], edge_pairs: list[tuple[str, str]]
) -> list[tuple[str, str, int]]:
    counts: Counter[tuple[str, str]] = Counter()
    for source_id, target_id in edge_pairs:
        source_node = nodes_by_id.get(source_id, {})
        target_node = nodes_by_id.get(target_id, {})
        source_file = str(source_node.get("file") or "").strip()
        target_file = str(target_node.get("file") or "").strip()
        if not source_file or not target_file or source_file == target_file:
            continue
        pair = tuple(sorted((source_file, target_file)))
        counts[pair] += 1
    return [
        (format_file_label(left), format_file_label(right), count)
        for (left, right), count in counts.most_common(6)
    ]


def collect_top_modules(nodes: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for node in nodes:
        file_path = str(node.get("file") or "").strip()
        if file_path:
            counts[format_file_label(file_path)] += 1
    return counts.most_common(6)


def collect_hotspots(nodes: list[dict[str, Any]]) -> list[str]:
    hotspot_nodes = [
        str(node.get("name") or "unknown")
        for node in nodes
        if str(node.get("mutation_status") or "").lower() == "hotspot"
    ]
    return hotspot_nodes[:8]


def build_evidence_items(
    graph_data: dict[str, Any] | None,
    selected_node: dict[str, Any] | None,
    callers: list[str] | None = None,
    callees: list[str] | None = None,
) -> list[str]:
    if graph_data is None:
        return []

    insights = graph_data.get("insights") or {}
    evidence: list[str] = []
    evidence_id = 1

    for coupling in insights.get("tight_coupling", [])[:3]:
        evidence.append(
            f"[E{evidence_id}] Tight coupling: {coupling.get('left')} <-> {coupling.get('right')} ({coupling.get('edges')} edges)"
        )
        evidence_id += 1

    for node in insights.get("fan_in", [])[:2]:
        evidence.append(
            f"[E{evidence_id}] High fan-in node: {node.get('name')} in {format_file_label(node.get('file') or '')} ({node.get('score')} inbound edges)"
        )
        evidence_id += 1

    if selected_node is not None:
        evidence.append(
            f"[E{evidence_id}] Selected node: {selected_node.get('name')} at {selected_node.get('file')}:{selected_node.get('line')}"
        )
        evidence_id += 1
        evidence.append(
            f"[E{evidence_id}] Selected summary: {selected_node.get('summary') or 'No summary available.'}"
        )
        evidence_id += 1
        if callers:
            evidence.append(f"[E{evidence_id}] Callers: {safe_join_names(callers)}")
            evidence_id += 1
        if callees:
            evidence.append(f"[E{evidence_id}] Callees: {safe_join_names(callees)}")
            evidence_id += 1

    for candidate in insights.get("dead_code_candidates", [])[:3]:
        evidence.append(
            f"[E{evidence_id}] Dead-code candidate: {candidate.get('name')} in {format_file_label(candidate.get('file') or '')}"
        )
        evidence_id += 1

    return evidence


def collect_feature_candidates(
    selected_node: dict[str, Any] | None,
    nodes_by_id: dict[str, dict[str, Any]],
    edge_pairs: list[tuple[str, str]],
) -> list[str]:
    module_counts: Counter[str] = Counter()
    if selected_node is not None:
        selected_file = str(selected_node.get("file") or "").strip()
        if selected_file:
            module_counts[format_file_label(selected_file)] += 4
        selected_id = str(selected_node.get("id") or "").strip()
        for source_id, target_id in edge_pairs:
            if source_id == selected_id:
                target_file = str((nodes_by_id.get(target_id) or {}).get("file") or "").strip()
                if target_file:
                    module_counts[format_file_label(target_file)] += 2
            if target_id == selected_id:
                source_file = str((nodes_by_id.get(source_id) or {}).get("file") or "").strip()
                if source_file:
                    module_counts[format_file_label(source_file)] += 2
    else:
        for module_name, count in collect_top_modules(list(nodes_by_id.values()))[:5]:
            module_counts[module_name] += count
    return [name for name, _ in module_counts.most_common(5)]


def build_project_context(graph_data: dict[str, Any] | None) -> str:
    if graph_data is None:
        return "No graph has been scanned yet."

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    nodes_by_id = build_node_index(graph_data)
    edge_pairs = build_edge_pairs(graph_data)
    coupled_modules = collect_module_coupling(nodes_by_id, edge_pairs)
    top_modules = collect_top_modules(nodes)
    hotspots = collect_hotspots(nodes)
    insights = graph_data.get("insights") or {}
    edge_breakdown = insights.get("edge_type_breakdown") or graph_data.get("meta", {}).get("edge_breakdown", {})

    lines = [
        f"Project stats: {len(nodes)} nodes, {len(edges)} edges.",
        f"Most populated modules: {safe_join_names([f'{name} ({count})' for name, count in top_modules], limit=6)}",
        f"Hotspot nodes: {safe_join_names(hotspots, limit=6)}",
        f"Edge types: {safe_join_names([f'{edge_type} ({count})' for edge_type, count in edge_breakdown.items()], limit=6)}",
    ]

    if coupled_modules:
        lines.append(
            "Most tightly coupled modules: "
            + safe_join_names(
                [f"{left} <-> {right} ({count} edges)" for left, right, count in coupled_modules],
                limit=5,
            )
        )
    else:
        lines.append("Most tightly coupled modules: None identified yet.")
    dead_code = insights.get("dead_code_candidates") or []
    lines.append(
        f"Dead-code candidates: {safe_join_names([item.get('name') or 'unknown' for item in dead_code], limit=6)}"
    )
    return "\n".join(lines)


def build_chat_context(graph_data: dict[str, Any] | None, node_id: str | None) -> str:
    if graph_data is None:
        return "No graph has been scanned yet."

    nodes = graph_data.get("nodes", [])
    nodes_by_id = build_node_index(graph_data)
    edge_pairs = build_edge_pairs(graph_data)
    lines = [build_project_context(graph_data)]

    if not node_id:
        lines.append("No specific node selected.")
        lines.append(
            "Answer project-level questions using graph structure, hotspots, and module coupling."
        )
        feature_candidates = collect_feature_candidates(None, nodes_by_id, edge_pairs)
        lines.append(
            f"Good candidate modules for new features: {safe_join_names(feature_candidates, limit=5)}"
        )
        evidence_items = build_evidence_items(graph_data, None)
        if evidence_items:
            lines.append("Evidence:")
            lines.extend(evidence_items)
        return "\n".join(lines)

    node = _get_node_from_graph(graph_data, node_id)
    if node is None:
        lines.append(f"Selected node id '{node_id}' was not found.")
        return "\n".join(lines)

    callers: list[str] = []
    callees: list[str] = []
    sibling_nodes: list[str] = []
    module_name = format_file_label(str(node.get("file") or ""))
    for item in nodes:
        if item.get("id") == node_id:
            continue
        if str(item.get("file") or "") == str(node.get("file") or ""):
            sibling_nodes.append(str(item.get("name") or "unknown"))
    for source_id, target_id in edge_pairs:
        if target_id == node_id:
            source_node = nodes_by_id.get(source_id)
            callers.append((source_node or {}).get("name") or source_id)
        if source_id == node_id:
            target_node = nodes_by_id.get(target_id)
            callees.append((target_node or {}).get("name") or target_id)

    blast_info = blast_radius.compute_blast_radius(graph_data, node_id)
    feature_candidates = collect_feature_candidates(node, nodes_by_id, edge_pairs)

    lines.extend(
        [
            "Selected node details:",
            f"- id: {node.get('id')}",
            f"- name: {node.get('name')}",
            f"- type: {node.get('type')}",
            f"- file: {node.get('file')}:{node.get('line')}",
            f"- module label: {module_name}",
            f"- summary: {node.get('summary') or 'No summary available.'}",
            f"- callers: {safe_join_names(callers)}",
            f"- callees: {safe_join_names(callees)}",
            f"- same-module neighbors: {safe_join_names(sibling_nodes)}",
            f"- blast radius: {blast_info.get('summary') or 'No blast radius available.'}",
            f"- recommended feature placement modules: {safe_join_names(feature_candidates, limit=5)}",
        ]
    )
    evidence_items = build_evidence_items(graph_data, node, callers, callees)
    if evidence_items:
        lines.append("Evidence:")
        lines.extend(evidence_items)
    return "\n".join(lines)


def build_chat_messages(
    graph_data: dict[str, Any] | None,
    message: str,
    node_id: str | None,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    context = build_chat_context(graph_data, node_id)
    system_prompt = (
        "You are CodeWeave Assistant, a helpful software architecture guide. "
        "Use only the provided context when stating project specifics. "
        "If context is missing, clearly say so and ask for a scan or a better question. "
        "Keep answers concise and actionable. "
        "When asked what breaks if code changes, use callers, callees, and blast radius. "
        "When asked where to add a feature, recommend likely modules or nodes and explain why. "
        "When asked which modules are tightly coupled, rely on module coupling data from the context. "
        "Prefer short bullet points or short paragraphs with evidence. "
        "Whenever you make a specific claim, cite one or more evidence ids like [E1] or [E2]."
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"Context:\n{context}"},
    ]
    for item in history[-8:]:
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    return messages


def chat_with_groq(messages: list[dict[str, str]], model: str) -> str:
    global CHAT_RATE_LIMIT_UNTIL
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or Groq is None:
        raise ValueError("Missing GROQ_API_KEY for chat")
    if CHAT_RATE_LIMIT_UNTIL > time.time():
        wait_seconds = max(1, int(CHAT_RATE_LIMIT_UNTIL - time.time()))
        raise ValueError(f"Groq is rate limited right now. Try again in about {wait_seconds}s.")

    client = Groq(api_key=api_key, max_retries=0)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=500,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "429" in message or "rate limit" in message or "too many requests" in message:
            CHAT_RATE_LIMIT_UNTIL = time.time() + CHAT_RATE_LIMIT_COOLDOWN_SECONDS
            raise ValueError("Groq is rate limited right now. Try again in about 30s.") from exc
        raise
    if not response.choices:
        return "No response generated."
    return (response.choices[0].message.content or "No response generated.").strip()


def chat_with_provider(
    graph_data: dict[str, Any] | None,
    provider: str,
    message: str,
    node_id: str | None,
    history: list[dict[str, str]],
    model: str,
) -> str:
    messages = build_chat_messages(graph_data=graph_data, message=message, node_id=node_id, history=history)
    if provider == "groq":
        return chat_with_groq(messages, model=model)
    raise ValueError(f"Unsupported chat provider: {provider}")
