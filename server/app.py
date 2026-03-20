from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from groq import Groq


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from git_tracker import mutation_tracker
from graph import blast_radius, graph_builder


load_dotenv()
logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
CORS(app)

GRAPH_CACHE: dict[str, list[dict[str, Any]]] | None = None
DEFAULT_CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", "groq").strip().lower()
DEFAULT_GROQ_MODEL = os.getenv("CHAT_MODEL", "llama-3.1-8b-instant").strip()


def _get_node_from_cache(node_id: str) -> dict[str, Any] | None:
    if GRAPH_CACHE is None:
        return None
    for node in GRAPH_CACHE.get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


def _safe_join_names(values: list[str], limit: int = 12) -> str:
    if not values:
        return "None"
    if len(values) <= limit:
        return ", ".join(values)
    shown = ", ".join(values[:limit])
    return f"{shown}, and {len(values) - limit} more"


def _build_node_index() -> dict[str, dict[str, Any]]:
    if GRAPH_CACHE is None:
        return {}
    return {
        str(node.get("id")): node
        for node in GRAPH_CACHE.get("nodes", [])
        if node.get("id") is not None
    }


def _build_edge_pairs() -> list[tuple[str, str]]:
    if GRAPH_CACHE is None:
        return []
    pairs: list[tuple[str, str]] = []
    for edge in GRAPH_CACHE.get("edges", []):
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if source and target:
            pairs.append((source, target))
    return pairs


def _format_file_label(file_path: str) -> str:
    normalized = str(file_path or "").replace("\\", "/").strip()
    if not normalized:
        return "unknown module"
    return normalized.split("/")[-1] or normalized


def _collect_module_coupling(
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
        (_format_file_label(left), _format_file_label(right), count)
        for (left, right), count in counts.most_common(6)
    ]


def _collect_top_modules(nodes: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for node in nodes:
        file_path = str(node.get("file") or "").strip()
        if file_path:
            counts[_format_file_label(file_path)] += 1
    return counts.most_common(6)


def _collect_hotspots(nodes: list[dict[str, Any]]) -> list[str]:
    hotspot_nodes = [
        str(node.get("name") or "unknown")
        for node in nodes
        if str(node.get("mutation_status") or "").lower() == "hotspot"
    ]
    return hotspot_nodes[:8]


def _collect_feature_candidates(
    selected_node: dict[str, Any] | None,
    nodes_by_id: dict[str, dict[str, Any]],
    edge_pairs: list[tuple[str, str]],
) -> list[str]:
    module_counts: Counter[str] = Counter()
    if selected_node is not None:
        selected_file = str(selected_node.get("file") or "").strip()
        if selected_file:
            module_counts[_format_file_label(selected_file)] += 4
        selected_id = str(selected_node.get("id") or "").strip()
        for source_id, target_id in edge_pairs:
            if source_id == selected_id:
                target_file = str((nodes_by_id.get(target_id) or {}).get("file") or "").strip()
                if target_file:
                    module_counts[_format_file_label(target_file)] += 2
            if target_id == selected_id:
                source_file = str((nodes_by_id.get(source_id) or {}).get("file") or "").strip()
                if source_file:
                    module_counts[_format_file_label(source_file)] += 2
    else:
        for module_name, count in _collect_top_modules(list(nodes_by_id.values()))[:5]:
            module_counts[module_name] += count
    return [name for name, _ in module_counts.most_common(5)]


def _build_project_context() -> str:
    if GRAPH_CACHE is None:
        return "No graph has been scanned yet."

    nodes = GRAPH_CACHE.get("nodes", [])
    edges = GRAPH_CACHE.get("edges", [])
    nodes_by_id = _build_node_index()
    edge_pairs = _build_edge_pairs()
    coupled_modules = _collect_module_coupling(nodes_by_id, edge_pairs)
    top_modules = _collect_top_modules(nodes)
    hotspots = _collect_hotspots(nodes)

    lines = [
        f"Project stats: {len(nodes)} nodes, {len(edges)} edges.",
        f"Most populated modules: {_safe_join_names([f'{name} ({count})' for name, count in top_modules], limit=6)}",
        f"Hotspot nodes: {_safe_join_names(hotspots, limit=6)}",
    ]

    if coupled_modules:
        lines.append(
            "Most tightly coupled modules: "
            + _safe_join_names(
                [f"{left} <-> {right} ({count} edges)" for left, right, count in coupled_modules],
                limit=5,
            )
        )
    else:
        lines.append("Most tightly coupled modules: None identified yet.")

    return "\n".join(lines)


def _build_chat_context(node_id: str | None) -> str:
    if GRAPH_CACHE is None:
        return "No graph has been scanned yet."

    nodes = GRAPH_CACHE.get("nodes", [])
    edges = GRAPH_CACHE.get("edges", [])
    nodes_by_id = _build_node_index()
    edge_pairs = _build_edge_pairs()
    lines = [_build_project_context()]

    if not node_id:
        lines.append("No specific node selected.")
        lines.append(
            "Answer project-level questions using graph structure, hotspots, and module coupling."
        )
        feature_candidates = _collect_feature_candidates(None, nodes_by_id, edge_pairs)
        lines.append(
            f"Good candidate modules for new features: {_safe_join_names(feature_candidates, limit=5)}"
        )
        return "\n".join(lines)

    node = _get_node_from_cache(node_id)
    if node is None:
        lines.append(f"Selected node id '{node_id}' was not found.")
        return "\n".join(lines)

    callers: list[str] = []
    callees: list[str] = []
    sibling_nodes: list[str] = []
    module_name = _format_file_label(str(node.get("file") or ""))
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

    blast_info = blast_radius.compute_blast_radius(GRAPH_CACHE, node_id)
    feature_candidates = _collect_feature_candidates(node, nodes_by_id, edge_pairs)

    lines.extend(
        [
            "Selected node details:",
            f"- id: {node.get('id')}",
            f"- name: {node.get('name')}",
            f"- type: {node.get('type')}",
            f"- file: {node.get('file')}:{node.get('line')}",
            f"- module label: {module_name}",
            f"- summary: {node.get('summary') or 'No summary available.'}",
            f"- callers: {_safe_join_names(callers)}",
            f"- callees: {_safe_join_names(callees)}",
            f"- same-module neighbors: {_safe_join_names(sibling_nodes)}",
            f"- blast radius: {blast_info.get('summary') or 'No blast radius available.'}",
            f"- recommended feature placement modules: {_safe_join_names(feature_candidates, limit=5)}",
        ]
    )
    return "\n".join(lines)


def _build_chat_messages(
    message: str,
    node_id: str | None,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    context = _build_chat_context(node_id)
    system_prompt = (
        "You are CodeWeave Assistant, a helpful software architecture guide. "
        "Use only the provided context when stating project specifics. "
        "If context is missing, clearly say so and ask for a scan or a better question. "
        "Keep answers concise and actionable. "
        "When asked what breaks if code changes, use callers, callees, and blast radius. "
        "When asked where to add a feature, recommend likely modules or nodes and explain why. "
        "When asked which modules are tightly coupled, rely on module coupling data from the context. "
        "Prefer short bullet points or short paragraphs with evidence."
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


def _chat_with_groq(messages: list[dict[str, str]]) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Missing GROQ_API_KEY for chat")

    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=DEFAULT_GROQ_MODEL,
        messages=messages,
        temperature=0.2,
        max_tokens=500,
    )
    if not response.choices:
        return "No response generated."
    return (response.choices[0].message.content or "No response generated.").strip()


def _chat_with_provider(
    provider: str,
    message: str,
    node_id: str | None,
    history: list[dict[str, str]],
) -> str:
    messages = _build_chat_messages(message=message, node_id=node_id, history=history)
    if provider == "groq":
        return _chat_with_groq(messages)
    raise ValueError(f"Unsupported chat provider: {provider}")


def _normalize_github_repo_url(url_value: str) -> str:
    parsed = urlparse(url_value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("GitHub URL must start with http:// or https://")
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Only github.com URLs are supported")

    path_parts = [segment for segment in parsed.path.split("/") if segment]
    if len(path_parts) < 2:
        raise ValueError("GitHub URL must include owner and repository name")

    owner = path_parts[0]
    repo = path_parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise ValueError("Invalid GitHub repository URL")

    return f"https://github.com/{owner}/{repo}.git"


def _clone_github_repo(repo_url: str, target_dir: Path) -> Path:
    try:
        result = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                repo_url,
                str(target_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except FileNotFoundError as exc:
        raise ValueError("Git is not installed or not available in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Timed out while cloning the GitHub repository") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        error_message = stderr or stdout or "Unknown git clone error"
        raise ValueError(f"Failed to clone repository: {error_message}")

    return target_dir


def _scan_repository(scan_root: Path) -> dict[str, list[dict[str, Any]]]:
    graph_data = graph_builder.build_graph(str(scan_root))
    graph_data["nodes"] = mutation_tracker.track_mutations(
        str(scan_root), graph_data.get("nodes", [])
    )
    return graph_data


@app.post("/api/scan")
def scan_project() -> Any:
    global GRAPH_CACHE
    try:
        payload = request.get_json(silent=True) or {}
        project_input = str(payload.get("path") or "").strip()
        if not project_input:
            return jsonify({"error": "Invalid project path"}), 400

        if project_input.startswith(("http://", "https://")):
            repo_url = _normalize_github_repo_url(project_input)
            with tempfile.TemporaryDirectory(prefix="codeweave_repo_") as temp_dir:
                clone_path = Path(temp_dir) / "repo"
                _clone_github_repo(repo_url, clone_path)
                graph_data = _scan_repository(clone_path)
        else:
            resolved_path = Path(project_input).expanduser().resolve()
            if not resolved_path.exists() or not resolved_path.is_dir():
                return jsonify({"error": "Invalid project path"}), 400
            graph_data = _scan_repository(resolved_path)

        GRAPH_CACHE = graph_data
        return jsonify(graph_data)
    except Exception as exc:
        LOGGER.exception("Scan failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/graph")
def get_graph() -> Any:
    try:
        if GRAPH_CACHE is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(GRAPH_CACHE)
    except Exception as exc:
        LOGGER.exception("Graph fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/node/<node_id>")
def get_node(node_id: str) -> Any:
    try:
        if GRAPH_CACHE is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        for node in GRAPH_CACHE.get("nodes", []):
            if node.get("id") == node_id:
                return jsonify(node)
        return jsonify({"error": "Node not found"}), 404
    except Exception as exc:
        LOGGER.exception("Node fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/blast/<node_id>")
def get_blast_radius(node_id: str) -> Any:
    try:
        if GRAPH_CACHE is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(blast_radius.compute_blast_radius(GRAPH_CACHE, node_id))
    except Exception as exc:
        LOGGER.exception("Blast radius failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.post("/api/chat")
def chat_about_node() -> Any:
    try:
        if GRAPH_CACHE is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message") or "").strip()
        node_id = str(payload.get("node_id") or "").strip() or None
        provider = str(payload.get("provider") or DEFAULT_CHAT_PROVIDER).strip().lower()
        history = (
            payload.get("history") if isinstance(payload.get("history"), list) else []
        )

        if not message:
            return jsonify({"error": "Message is required"}), 400

        answer = _chat_with_provider(
            provider=provider,
            message=message,
            node_id=node_id,
            history=history,
        )
        return jsonify(
            {
                "provider": provider,
                "node_id": node_id,
                "answer": answer,
            }
        )
    except Exception as exc:
        LOGGER.exception("Chat failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/")
def serve_index() -> Any:
    try:
        return send_from_directory(str(FRONTEND_DIR), "index.html")
    except Exception as exc:
        LOGGER.exception("Failed to serve index: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/<path:asset_path>")
def serve_frontend_asset(asset_path: str) -> Any:
    try:
        return send_from_directory(str(FRONTEND_DIR), asset_path)
    except Exception as exc:
        LOGGER.exception("Failed to serve asset %s: %s", asset_path, exc)
        return jsonify({"error": str(exc)}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
