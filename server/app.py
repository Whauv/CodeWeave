from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        logging.getLogger(__name__).warning(
            "python-dotenv is unavailable; continuing without loading .env automatically."
        )
        return False


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graph import blast_radius
from plugins import get_language_options, get_plugin
from server.action_plan_service import build_action_plan
from server.chat_service import chat_with_provider
from server.repository_service import (
    diff_commits,
    ensure_cached_repo,
    extract_commit_snapshot,
    is_git_repo,
    list_repo_commits,
    resolve_scan_source,
)
from server.state import STATE


load_dotenv()
logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("groq").setLevel(logging.WARNING)
logging.getLogger("groq._base_client").setLevel(logging.ERROR)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
CORS(app)

DEFAULT_CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", "groq").strip().lower()
DEFAULT_GROQ_MODEL = os.getenv("CHAT_MODEL", "llama-3.1-8b-instant").strip()


def _scan_repository(scan_root: Path, language: str, **options: Any) -> dict[str, Any]:
    plugin = get_plugin(language)
    return plugin.scan(str(scan_root), **options)


@app.get("/api/languages")
def get_languages() -> Any:
    try:
        return jsonify({"languages": get_language_options()})
    except Exception as exc:
        LOGGER.exception("Failed to fetch languages: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.post("/api/scan")
def scan_project() -> Any:
    try:
        payload = request.get_json(silent=True) or {}
        project_input = str(payload.get("path") or "").strip()
        language = str(payload.get("language") or "python").strip().lower()
        if not project_input:
            return jsonify({"error": "Invalid project path"}), 400

        scan_root, normalized_target, source_kind = resolve_scan_source(project_input)
        graph_data = _scan_repository(scan_root, language)

        STATE.graph_cache = graph_data
        STATE.scan_context = {
            "project_input": project_input,
            "target": normalized_target,
            "scan_root": str(scan_root),
            "language": language,
            "is_git_repo": is_git_repo(scan_root),
            "source_kind": source_kind,
        }
        STATE.history_graph_cache.clear()
        return jsonify(graph_data)
    except Exception as exc:
        LOGGER.exception("Scan failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/graph")
def get_graph() -> Any:
    try:
        if STATE.graph_cache is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(STATE.graph_cache)
    except Exception as exc:
        LOGGER.exception("Graph fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/insights")
def get_insights() -> Any:
    try:
        if STATE.graph_cache is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(STATE.graph_cache.get("insights", {}))
    except Exception as exc:
        LOGGER.exception("Insights fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/node/<node_id>")
def get_node(node_id: str) -> Any:
    try:
        if STATE.graph_cache is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        for node in STATE.graph_cache.get("nodes", []):
            if node.get("id") == node_id:
                return jsonify(node)
        return jsonify({"error": "Node not found"}), 404
    except Exception as exc:
        LOGGER.exception("Node fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/blast/<node_id>")
def get_blast_radius(node_id: str) -> Any:
    try:
        if STATE.graph_cache is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(blast_radius.compute_blast_radius(STATE.graph_cache, node_id))
    except Exception as exc:
        LOGGER.exception("Blast radius failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/action-plan/<node_id>")
def get_action_plan(node_id: str) -> Any:
    try:
        if STATE.graph_cache is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        plan = build_action_plan(STATE.graph_cache, node_id)
        if plan.get("error"):
            return jsonify({"error": str(plan.get("error"))}), 404
        return jsonify(plan)
    except Exception as exc:
        LOGGER.exception("Action plan generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/action/plan/<node_id>")
def get_action_plan_legacy_slash(node_id: str) -> Any:
    return get_action_plan(node_id)


@app.get("/api/action_plan/<node_id>")
def get_action_plan_legacy_underscore(node_id: str) -> Any:
    return get_action_plan(node_id)


@app.post("/api/chat")
def chat_about_node() -> Any:
    try:
        if STATE.graph_cache is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message") or "").strip()
        node_id = str(payload.get("node_id") or "").strip() or None
        provider = str(payload.get("provider") or DEFAULT_CHAT_PROVIDER).strip().lower()
        history = payload.get("history") if isinstance(payload.get("history"), list) else []

        if not message:
            return jsonify({"error": "Message is required"}), 400

        answer = chat_with_provider(
            graph_data=STATE.graph_cache,
            provider=provider,
            message=message,
            node_id=node_id,
            history=history,
            model=DEFAULT_GROQ_MODEL,
        )
        return jsonify({"provider": provider, "node_id": node_id, "answer": answer})
    except Exception as exc:
        LOGGER.exception("Chat failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/history")
def get_time_travel_commits() -> Any:
    try:
        if STATE.scan_context is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        source_kind = str(STATE.scan_context.get("source_kind") or "local")
        repo_root = Path(str(STATE.scan_context.get("scan_root") or "")).resolve()
        if source_kind == "github":
            repo_url = str(STATE.scan_context.get("target") or "").strip()
            if repo_url:
                repo_root = ensure_cached_repo(repo_url, include_all_branches=True)

        commits, history_meta = list_repo_commits(repo_root)
        return jsonify(
            {
                "target": STATE.scan_context.get("target"),
                "language": STATE.scan_context.get("language"),
                "source_kind": source_kind,
                "commits": commits,
                "history_meta": history_meta,
            }
        )
    except Exception as exc:
        LOGGER.exception("History fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/history/<commit_hash>")
def get_history_graph(commit_hash: str) -> Any:
    snapshot_root: Path | None = None
    try:
        if STATE.scan_context is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        repo_root = Path(str(STATE.scan_context.get("scan_root") or "")).resolve()
        language = str(STATE.scan_context.get("language") or "python")
        cache_key = f"{repo_root}::{language}::{commit_hash}"
        if cache_key in STATE.history_graph_cache:
            return jsonify(STATE.history_graph_cache[cache_key])

        snapshot_root = extract_commit_snapshot(repo_root, commit_hash)
        graph_data = _scan_repository(
            snapshot_root,
            language,
            include_summaries=False,
            include_mutation_tracking=False,
        )
        graph_data.setdefault("meta", {})
        graph_data["meta"].update({"history_commit": commit_hash, "history_mode": True})
        STATE.history_graph_cache[cache_key] = graph_data
        return jsonify(graph_data)
    except Exception as exc:
        LOGGER.exception("History graph fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400
    finally:
        if snapshot_root is not None:
            shutil.rmtree(snapshot_root.parent, ignore_errors=True)


@app.get("/api/history-diff/<from_commit>/<to_commit>")
def get_history_diff(from_commit: str, to_commit: str) -> Any:
    try:
        if STATE.scan_context is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        repo_root = Path(str(STATE.scan_context.get("scan_root") or "")).resolve()
        if not is_git_repo(repo_root):
            return jsonify({"error": "Time-travel diff requires a git repository."}), 400

        diff_data = diff_commits(repo_root, from_commit, to_commit)
        return jsonify(diff_data)
    except Exception as exc:
        LOGGER.exception("History diff fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/history/diff/<from_commit>/<to_commit>")
def get_history_diff_legacy(from_commit: str, to_commit: str) -> Any:
    return get_history_diff(from_commit, to_commit)


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
    debug_enabled = os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=5050, debug=debug_enabled, use_reloader=False)
