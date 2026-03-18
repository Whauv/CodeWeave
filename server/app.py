from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
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


def _build_chat_context(node_id: str | None) -> str:
    if GRAPH_CACHE is None:
        return "No graph has been scanned yet."

    nodes = GRAPH_CACHE.get("nodes", [])
    edges = GRAPH_CACHE.get("edges", [])
    lines = [
        f"Project stats: {len(nodes)} nodes, {len(edges)} edges.",
    ]

    if not node_id:
        lines.append("No specific node selected.")
        lines.append("Answer project-level questions using the available graph data.")
        return "\n".join(lines)

    node = _get_node_from_cache(node_id)
    if node is None:
        lines.append(f"Selected node id '{node_id}' was not found.")
        return "\n".join(lines)

    callers: list[str] = []
    callees: list[str] = []
    node_by_id = {item.get("id"): item for item in nodes}
    for edge in edges:
        if edge.get("target") == node_id:
            source_node = node_by_id.get(edge.get("source"))
            callers.append((source_node or {}).get("name") or str(edge.get("source")))
        if edge.get("source") == node_id:
            target_node = node_by_id.get(edge.get("target"))
            callees.append((target_node or {}).get("name") or str(edge.get("target")))

    lines.extend(
        [
            "Selected node details:",
            f"- id: {node.get('id')}",
            f"- name: {node.get('name')}",
            f"- type: {node.get('type')}",
            f"- file: {node.get('file')}:{node.get('line')}",
            f"- summary: {node.get('summary') or 'No summary available.'}",
            f"- callers: {_safe_join_names(callers)}",
            f"- callees: {_safe_join_names(callees)}",
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
        "Keep answers concise and actionable."
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
