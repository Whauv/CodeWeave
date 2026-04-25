from __future__ import annotations

import logging
import os
import shutil
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge

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
from server import db
from server.action_plan_service import build_action_plan
from server.auth import get_request_identity, require_auth, set_request_identity
from server.chat_service import chat_with_provider
from server.errors import ApiError, error_response, from_api_error
from server.jobs import JOBS
from server.logging_config import REQUEST_ID, configure_logging
from server.rate_limit import rate_limit
from server.repository_service import (
    DEFAULT_ALLOWED_GITHUB_HOSTS,
    diff_commits,
    ensure_cached_repo,
    extract_commit_snapshot,
    get_head_commit_hash,
    is_git_repo,
    list_repo_commits,
    resolve_scan_source,
)
from server.schemas import parse_chat_request, parse_scan_request, validate_commit_hash
from server.snapshot_cache import (
    load_history_snapshot,
    load_scan_snapshot,
    save_history_snapshot,
    save_scan_snapshot,
)
from server.state import STATE


load_dotenv()
configure_logging()
LOGGER = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("groq").setLevel(logging.WARNING)
logging.getLogger("groq._base_client").setLevel(logging.ERROR)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
CORS(app)
db.init_db()

DEFAULT_CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", "groq").strip().lower()
DEFAULT_GROQ_MODEL = os.getenv("CHAT_MODEL", "llama-3.1-8b-instant").strip()
SCAN_RATE_LIMIT = int(os.getenv("CODEWEAVE_RATE_LIMIT_SCAN", "10"))
CHAT_RATE_LIMIT = int(os.getenv("CODEWEAVE_RATE_LIMIT_CHAT", "20"))
HISTORY_RATE_LIMIT = int(os.getenv("CODEWEAVE_RATE_LIMIT_HISTORY", "30"))
RATE_WINDOW_SECONDS = int(os.getenv("CODEWEAVE_RATE_LIMIT_WINDOW", "60"))
SCAN_TIMEOUT_SECONDS = max(10, int(os.getenv("CODEWEAVE_SCAN_TIMEOUT_SECONDS", "180")))
HISTORY_TIMEOUT_SECONDS = max(10, int(os.getenv("CODEWEAVE_HISTORY_TIMEOUT_SECONDS", "180")))
MAX_REQUEST_BYTES = max(1024, int(os.getenv("CODEWEAVE_MAX_REQUEST_BYTES", str(1024 * 1024))))
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES


def _parse_allowed_local_roots() -> list[Path]:
    raw_value = os.getenv("CODEWEAVE_ALLOWED_LOCAL_ROOTS", "").strip()
    if not raw_value:
        return []
    roots: list[Path] = []
    for raw_entry in raw_value.split(os.pathsep):
        entry = raw_entry.strip()
        if not entry:
            continue
        path = Path(entry).expanduser().resolve()
        if path.exists() and path.is_dir():
            roots.append(path)
        else:
            LOGGER.warning("Ignoring non-existent allowed scan root: %s", entry)
    return roots


def _parse_allowed_github_hosts() -> set[str]:
    raw_value = os.getenv("CODEWEAVE_ALLOWED_GITHUB_HOSTS", "").strip()
    if not raw_value:
        return set(DEFAULT_ALLOWED_GITHUB_HOSTS)
    hosts = {item.strip().lower() for item in raw_value.split(",") if item.strip()}
    return hosts or set(DEFAULT_ALLOWED_GITHUB_HOSTS)


ALLOWED_LOCAL_ROOTS = _parse_allowed_local_roots()
ALLOWED_GITHUB_HOSTS = _parse_allowed_github_hosts()


@app.errorhandler(ApiError)
def handle_api_error(exc: ApiError) -> Any:
    return from_api_error(exc)


@app.errorhandler(RequestEntityTooLarge)
def handle_large_payload(_exc: RequestEntityTooLarge) -> Any:
    return error_response(
        "payload_too_large",
        f"Request body exceeds max size of {MAX_REQUEST_BYTES} bytes.",
        413,
    )


@app.before_request
def _setup_request_context() -> None:
    request_id = str(request.headers.get("X-Request-Id") or uuid.uuid4())
    REQUEST_ID.set(request_id)
    identity_hint = str(request.headers.get("X-Codeweave-User") or request.remote_addr or "anonymous")
    set_request_identity(identity_hint)
    STATE.reset()


def _run_with_timeout(
    *,
    timeout_seconds: int,
    failure_code: str,
    failure_message: str,
    fn: Any,
    args: tuple[Any, ...] = (),
    **kwargs: Any,
) -> Any:
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="codeweave-timeout-guard") as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            raise ApiError(failure_code, failure_message, 504) from exc


def _scan_repository(scan_root: Path, language: str, timeout_seconds: int, **options: Any) -> dict[str, Any]:
    plugin = get_plugin(language)
    return _run_with_timeout(
        timeout_seconds=timeout_seconds,
        failure_code="scan_timeout",
        failure_message=f"Scan timed out after {timeout_seconds}s.",
        fn=plugin.scan,
        args=(str(scan_root),),
        **options,
    )


def _load_latest_scan(identity: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if STATE.graph_cache is not None and STATE.scan_context is not None:
        return STATE.graph_cache, STATE.scan_context
    persisted = db.get_latest_scan_for_identity(identity)
    if not persisted:
        return None, None
    graph_data = persisted.get("graph_data")
    scan_context = persisted.get("scan_context")
    if isinstance(graph_data, dict) and isinstance(scan_context, dict):
        STATE.graph_cache = graph_data
        STATE.scan_context = scan_context
        return graph_data, scan_context
    return None, None


def _persist_scan(identity: str, graph_data: dict[str, Any], scan_context: dict[str, Any]) -> None:
    db.save_scan_artifact(
        identity=identity,
        target=str(scan_context.get("target") or ""),
        source_kind=str(scan_context.get("source_kind") or "local"),
        language=str(scan_context.get("language") or "python"),
        graph_data=graph_data,
        scan_context=scan_context,
    )
    STATE.graph_cache = graph_data
    STATE.scan_context = scan_context
    STATE.history_graph_cache.clear()


def _perform_scan(identity: str, path_value: str, language: str) -> dict[str, Any]:
    scan_root, normalized_target, source_kind = resolve_scan_source(
        path_value,
        allowed_local_roots=ALLOWED_LOCAL_ROOTS,
        allowed_github_hosts=ALLOWED_GITHUB_HOSTS,
    )
    revision = get_head_commit_hash(scan_root) if is_git_repo(scan_root) else ""
    previous_graph, previous_context = _load_latest_scan(identity)

    if (
        previous_graph is not None
        and previous_context is not None
        and str(previous_context.get("target") or "") == normalized_target
        and str(previous_context.get("language") or "") == language
        and str(previous_context.get("scan_revision") or "") == revision
        and revision
    ):
        previous_graph.setdefault("meta", {})
        previous_graph["meta"]["incremental_reused"] = True
        return previous_graph

    if revision:
        disk_cached = load_scan_snapshot(
            target=normalized_target,
            language=language,
            revision=revision,
            source_kind=source_kind,
        )
        if isinstance(disk_cached, dict):
            disk_cached.setdefault("meta", {})
            disk_cached["meta"]["incremental_reused"] = True
            scan_context = {
                "project_input": path_value,
                "target": normalized_target,
                "scan_root": str(scan_root),
                "language": language,
                "is_git_repo": True,
                "source_kind": source_kind,
                "scan_revision": revision,
            }
            _persist_scan(identity, disk_cached, scan_context)
            return disk_cached

    graph_data = _scan_repository(scan_root, language, timeout_seconds=SCAN_TIMEOUT_SECONDS)
    graph_data.setdefault("meta", {})
    graph_data["meta"]["incremental_reused"] = False
    scan_context = {
        "project_input": path_value,
        "target": normalized_target,
        "scan_root": str(scan_root),
        "language": language,
        "is_git_repo": is_git_repo(scan_root),
        "source_kind": source_kind,
        "scan_revision": revision,
    }
    if revision:
        save_scan_snapshot(
            target=normalized_target,
            language=language,
            revision=revision,
            source_kind=source_kind,
            graph_data=graph_data,
        )
    _persist_scan(identity, graph_data, scan_context)
    return graph_data


def _perform_history_snapshot(identity: str, commit_hash: str) -> dict[str, Any]:
    graph_data, scan_context = _load_latest_scan(identity)
    if scan_context is None:
        raise ApiError("graph_not_scanned", "No graph scanned yet", 404)

    snapshot_root: Path | None = None
    try:
        validated_commit_hash = validate_commit_hash(commit_hash)
        repo_root = Path(str(scan_context.get("scan_root") or "")).resolve()
        language = str(scan_context.get("language") or "python")
        cached_history = load_history_snapshot(repo_root=repo_root, commit_hash=validated_commit_hash, language=language)
        if isinstance(cached_history, dict):
            cached_history.setdefault("meta", {})
            cached_history["meta"]["history_cached"] = True
            return cached_history
        snapshot_root = extract_commit_snapshot(repo_root, validated_commit_hash)
        history_graph = _scan_repository(
            snapshot_root,
            language,
            timeout_seconds=HISTORY_TIMEOUT_SECONDS,
            include_summaries=False,
            include_mutation_tracking=False,
        )
        history_graph.setdefault("meta", {})
        history_graph["meta"].update(
            {"history_commit": validated_commit_hash, "history_mode": True, "history_cached": False}
        )
        save_history_snapshot(
            repo_root=repo_root,
            commit_hash=validated_commit_hash,
            language=language,
            graph_data=history_graph,
        )
        return history_graph
    finally:
        if snapshot_root is not None:
            shutil.rmtree(snapshot_root.parent, ignore_errors=True)


def _scan_job_handler(identity: str, payload: dict[str, Any]) -> dict[str, Any]:
    path_value = str(payload.get("path") or "").strip()
    language = str(payload.get("language") or "python").strip().lower()
    return _perform_scan(identity, path_value, language)


def _history_snapshot_job_handler(identity: str, payload: dict[str, Any]) -> dict[str, Any]:
    commit_hash = str(payload.get("commit_hash") or "").strip()
    return _perform_history_snapshot(identity, commit_hash)


def _scan_endpoint_impl() -> Any:
    identity = get_request_identity()
    request_data = parse_scan_request(request.get_json(silent=True))
    graph_data = _perform_scan(identity, request_data.path, request_data.language)
    return jsonify(graph_data)


def _chat_endpoint_impl() -> Any:
    identity = get_request_identity()
    graph_data, _scan_context = _load_latest_scan(identity)
    if graph_data is None:
        return error_response("graph_not_scanned", "No graph scanned yet", 404)

    request_data = parse_chat_request(request.get_json(silent=True), default_provider=DEFAULT_CHAT_PROVIDER)
    answer = chat_with_provider(
        graph_data=graph_data,
        provider=request_data.provider,
        message=request_data.message,
        node_id=request_data.node_id,
        history=request_data.history,
        model=DEFAULT_GROQ_MODEL,
    )
    return jsonify({"provider": request_data.provider, "node_id": request_data.node_id, "answer": answer})


def _history_commits_impl() -> Any:
    identity = get_request_identity()
    _graph_data, scan_context = _load_latest_scan(identity)
    if scan_context is None:
        return error_response("graph_not_scanned", "No graph scanned yet", 404)

    source_kind = str(scan_context.get("source_kind") or "local")
    repo_root = Path(str(scan_context.get("scan_root") or "")).resolve()
    if source_kind == "github":
        repo_url = str(scan_context.get("target") or "").strip()
        if repo_url:
            repo_root = ensure_cached_repo(repo_url, include_all_branches=True)

    commits, history_meta = list_repo_commits(repo_root)
    return jsonify(
        {
            "target": scan_context.get("target"),
            "language": scan_context.get("language"),
            "source_kind": source_kind,
            "commits": commits,
            "history_meta": history_meta,
        }
    )


def _history_snapshot_impl(commit_hash: str) -> Any:
    identity = get_request_identity()
    history_graph = _perform_history_snapshot(identity, commit_hash)
    return jsonify(history_graph)


def _history_diff_impl(from_commit: str, to_commit: str) -> Any:
    identity = get_request_identity()
    _graph_data, scan_context = _load_latest_scan(identity)
    if scan_context is None:
        return error_response("graph_not_scanned", "No graph scanned yet", 404)

    validated_from = validate_commit_hash(from_commit, field_name="from_commit")
    validated_to = validate_commit_hash(to_commit, field_name="to_commit")
    repo_root = Path(str(scan_context.get("scan_root") or "")).resolve()
    if not is_git_repo(repo_root):
        return error_response("history_diff_requires_git_repo", "Time-travel diff requires a git repository.", 400)

    diff_data = diff_commits(repo_root, validated_from, validated_to)
    return jsonify(diff_data)


@app.get("/health/live")
def health_live() -> Any:
    return jsonify({"status": "ok", "service": "codeweave"})


@app.get("/health/ready")
def health_ready() -> Any:
    db.init_db()
    return jsonify({"status": "ready", "db": str(db.DB_PATH)})


@app.get("/api/languages")
def get_languages() -> Any:
    try:
        return jsonify({"languages": get_language_options()})
    except Exception as exc:
        LOGGER.exception("Failed to fetch languages: %s", exc)
        return error_response("languages_failed", "Failed to fetch languages.", 500)


@app.post("/api/v1/scan")
@require_auth
@rate_limit(SCAN_RATE_LIMIT, RATE_WINDOW_SECONDS)
def scan_project_v1() -> Any:
    try:
        return _scan_endpoint_impl()
    except ApiError as exc:
        return from_api_error(exc)
    except ValueError as exc:
        return error_response("scan_source_validation_failed", str(exc), 400)
    except Exception as exc:
        LOGGER.exception("Scan failed: %s", exc)
        return error_response("scan_failed", "Scan failed due to an internal error.", 500)


@app.post("/api/scan")
def scan_project_legacy() -> Any:
    return scan_project_v1()


@app.post("/api/v1/jobs/scan")
@require_auth
@rate_limit(SCAN_RATE_LIMIT, RATE_WINDOW_SECONDS)
def create_scan_job() -> Any:
    try:
        request_data = parse_scan_request(request.get_json(silent=True))
        payload = {"path": request_data.path, "language": request_data.language}
        job_id = JOBS.submit(identity=get_request_identity(), job_type="scan", payload=payload, handler=_scan_job_handler)
        return jsonify({"job_id": job_id, "status": "queued"}), 202
    except ApiError as exc:
        return from_api_error(exc)


@app.post("/api/v1/jobs/history-snapshot")
@require_auth
@rate_limit(HISTORY_RATE_LIMIT, RATE_WINDOW_SECONDS)
def create_history_snapshot_job() -> Any:
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise ApiError("invalid_request_body", "Request body must be a JSON object.", 400)
        commit_hash = validate_commit_hash(str(payload.get("commit_hash") or ""))
        job_id = JOBS.submit(
            identity=get_request_identity(),
            job_type="history_snapshot",
            payload={"commit_hash": commit_hash},
            handler=_history_snapshot_job_handler,
        )
        return jsonify({"job_id": job_id, "status": "queued"}), 202
    except ApiError as exc:
        return from_api_error(exc)


@app.get("/api/v1/jobs/<job_id>")
@require_auth
def get_job_status(job_id: str) -> Any:
    record = JOBS.get_status(identity=get_request_identity(), job_id=job_id)
    if not record:
        return error_response("job_not_found", "Job not found.", 404)
    return jsonify(record)


@app.get("/api/v1/jobs/<job_id>/result")
@require_auth
def get_job_result(job_id: str) -> Any:
    record = JOBS.get_result(identity=get_request_identity(), job_id=job_id)
    if not record:
        return error_response("job_not_found", "Job not found.", 404)
    status = str(record.get("status") or "queued")
    if status in {"queued", "running"}:
        return jsonify({"id": job_id, "status": status}), 202
    if status == "failed":
        return error_response("job_failed", str(record.get("error_message") or "Job failed."), 400)
    return jsonify(record.get("result") or {})


@app.get("/api/graph")
def get_graph() -> Any:
    try:
        graph_data, _scan_context = _load_latest_scan(get_request_identity())
        if graph_data is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(graph_data)
    except Exception as exc:
        LOGGER.exception("Graph fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/insights")
def get_insights() -> Any:
    try:
        graph_data, _scan_context = _load_latest_scan(get_request_identity())
        if graph_data is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(graph_data.get("insights", {}))
    except Exception as exc:
        LOGGER.exception("Insights fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/node/<node_id>")
def get_node(node_id: str) -> Any:
    try:
        graph_data, _scan_context = _load_latest_scan(get_request_identity())
        if graph_data is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        for node in graph_data.get("nodes", []):
            if node.get("id") == node_id:
                return jsonify(node)
        return jsonify({"error": "Node not found"}), 404
    except Exception as exc:
        LOGGER.exception("Node fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/blast/<node_id>")
def get_blast_radius(node_id: str) -> Any:
    try:
        graph_data, _scan_context = _load_latest_scan(get_request_identity())
        if graph_data is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(blast_radius.compute_blast_radius(graph_data, node_id))
    except Exception as exc:
        LOGGER.exception("Blast radius failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/action-plan/<node_id>")
def get_action_plan(node_id: str) -> Any:
    try:
        graph_data, _scan_context = _load_latest_scan(get_request_identity())
        if graph_data is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        plan = build_action_plan(graph_data, node_id)
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


@app.post("/api/v1/chat")
@require_auth
@rate_limit(CHAT_RATE_LIMIT, RATE_WINDOW_SECONDS)
def chat_about_node_v1() -> Any:
    try:
        return _chat_endpoint_impl()
    except ApiError as exc:
        return from_api_error(exc)
    except Exception as exc:
        LOGGER.exception("Chat failed: %s", exc)
        return error_response("chat_failed", "Chat failed due to an internal error.", 500)


@app.post("/api/chat")
def chat_about_node_legacy() -> Any:
    return chat_about_node_v1()


@app.get("/api/v1/history")
@require_auth
@rate_limit(HISTORY_RATE_LIMIT, RATE_WINDOW_SECONDS)
def get_time_travel_commits_v1() -> Any:
    try:
        return _history_commits_impl()
    except ApiError as exc:
        return from_api_error(exc)
    except ValueError as exc:
        return error_response("history_unavailable", str(exc), 400)
    except Exception as exc:
        LOGGER.exception("History fetch failed: %s", exc)
        return error_response("history_fetch_failed", "Failed to fetch history.", 500)


@app.get("/api/history")
def get_time_travel_commits_legacy() -> Any:
    return get_time_travel_commits_v1()


@app.get("/api/v1/history/<commit_hash>")
@require_auth
@rate_limit(HISTORY_RATE_LIMIT, RATE_WINDOW_SECONDS)
def get_history_graph_v1(commit_hash: str) -> Any:
    try:
        return _history_snapshot_impl(commit_hash)
    except ApiError as exc:
        return from_api_error(exc)
    except ValueError as exc:
        return error_response("history_snapshot_failed", str(exc), 400)
    except Exception as exc:
        LOGGER.exception("History graph fetch failed: %s", exc)
        return error_response("history_snapshot_failed", "Failed to load history snapshot.", 500)


@app.get("/api/history/<commit_hash>")
def get_history_graph_legacy(commit_hash: str) -> Any:
    return get_history_graph_v1(commit_hash)


@app.get("/api/v1/history-diff/<from_commit>/<to_commit>")
@require_auth
@rate_limit(HISTORY_RATE_LIMIT, RATE_WINDOW_SECONDS)
def get_history_diff_v1(from_commit: str, to_commit: str) -> Any:
    try:
        return _history_diff_impl(from_commit, to_commit)
    except ApiError as exc:
        return from_api_error(exc)
    except ValueError as exc:
        return error_response("history_diff_failed", str(exc), 400)
    except Exception as exc:
        LOGGER.exception("History diff fetch failed: %s", exc)
        return error_response("history_diff_failed", "Failed to load history diff.", 500)


@app.get("/api/history-diff/<from_commit>/<to_commit>")
def get_history_diff_legacy(from_commit: str, to_commit: str) -> Any:
    return get_history_diff_v1(from_commit, to_commit)


@app.get("/api/history/diff/<from_commit>/<to_commit>")
def get_history_diff_legacy_alt(from_commit: str, to_commit: str) -> Any:
    return get_history_diff_v1(from_commit, to_commit)


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
    debug_enabled = os.getenv("FLASK_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
    port = int(os.getenv("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=debug_enabled, use_reloader=False)
