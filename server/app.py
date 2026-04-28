from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

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
from server.auth import (
    build_security_tokens,
    get_request_identity,
    issue_security_cookies,
    require_auth,
    resolve_untrusted_identity,
    set_request_identity,
)
from server.chat_service import chat_with_provider
from server.errors import ApiError, error_response, from_api_error
from server.jobs import JOBS
from server.logging_config import REQUEST_ID, configure_logging
from server.metrics import METRICS
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
    run_git_command,
)
from server.schemas import parse_chat_request, parse_scan_request, validate_commit_hash
from server.sentry_config import capture_exception, init_sentry
from server.snapshot_cache import (
    load_history_snapshot,
    load_scan_snapshot,
    save_history_snapshot,
    save_scan_snapshot,
)
from server.state import STATE


load_dotenv()
configure_logging()
init_sentry()
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
SCAN_TTL_DAYS = max(1, int(os.getenv("CODEWEAVE_SCAN_TTL_DAYS", "30")))
JOB_TTL_DAYS = max(1, int(os.getenv("CODEWEAVE_JOB_TTL_DAYS", "14")))
AUDIT_TTL_DAYS = max(1, int(os.getenv("CODEWEAVE_AUDIT_TTL_DAYS", "30")))
SHARE_LINK_TTL_HOURS = max(1, int(os.getenv("CODEWEAVE_SHARE_LINK_TTL_HOURS", "168")))
INVESTIGATION_TTL_DAYS = max(1, int(os.getenv("CODEWEAVE_INVESTIGATION_TTL_DAYS", "30")))
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
PR_URL_PATTERN = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:/.*)?$",
    flags=re.IGNORECASE,
)


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
try:
    cleanup_summary = db.cleanup_expired_artifacts(
        scan_ttl_days=SCAN_TTL_DAYS,
        job_ttl_days=JOB_TTL_DAYS,
        audit_ttl_days=AUDIT_TTL_DAYS,
    )
    LOGGER.info("Startup TTL cleanup complete: %s", cleanup_summary)
except Exception as cleanup_error:
    LOGGER.warning("Startup TTL cleanup failed: %s", cleanup_error)


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
    identity_hint = resolve_untrusted_identity()
    set_request_identity(identity_hint)
    STATE.reset()
    request.__codeweave_started_at = time.perf_counter()


@app.after_request
def _finalize_request(response: Any) -> Any:
    route = request.url_rule.rule if request.url_rule else request.path
    started_at = float(getattr(request, "__codeweave_started_at", time.perf_counter()))
    METRICS.observe_request(
        method=request.method,
        route=route,
        status_code=int(getattr(response, "status_code", 0) or 0),
        duration_seconds=max(0.0, time.perf_counter() - started_at),
    )
    try:
        identity = get_request_identity()
        if identity and identity != "anonymous":
            response = issue_security_cookies(response, identity)
    except Exception:
        pass

    try:
        if str(request.path).startswith("/api/"):
            identity = get_request_identity()
            metadata = {
                "method": request.method,
                "path": request.path,
                "request_id": REQUEST_ID.get("-"),
            }
            db.save_audit_event(
                identity=identity,
                action=f"{request.method} {request.path}",
                target=str(request.path),
                status_code=int(getattr(response, "status_code", 0) or 0),
                metadata=metadata,
            )
    except Exception as audit_error:
        LOGGER.debug("Audit write skipped: %s", audit_error)
    return response


@app.errorhandler(Exception)
def handle_unexpected_error(exc: Exception) -> Any:
    if isinstance(exc, HTTPException):
        return exc
    capture_exception(exc, path=request.path, method=request.method, request_id=REQUEST_ID.get("-"))
    LOGGER.exception("Unhandled error: %s", exc)
    return error_response("internal_server_error", "Unexpected server error.", 500)


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


def _resolve_history_scan_context(
    identity: str,
    *,
    target_override: str = "",
    language_override: str = "",
) -> dict[str, Any] | None:
    _graph_data, scan_context = _load_latest_scan(identity)
    target_value = str(target_override or "").strip()
    language_value = str(language_override or "").strip().lower()

    if not target_value:
        return scan_context

    if isinstance(scan_context, dict) and str(scan_context.get("target") or "").strip() == target_value:
        if language_value:
            scan_context = {**scan_context, "language": language_value}
        return scan_context

    scan_root, normalized_target, source_kind = resolve_scan_source(
        target_value,
        include_all_branches=True,
        allowed_local_roots=ALLOWED_LOCAL_ROOTS,
        allowed_github_hosts=ALLOWED_GITHUB_HOSTS,
    )
    resolved_language = language_value or str(scan_context.get("language") if isinstance(scan_context, dict) else "python")
    if not resolved_language:
        resolved_language = "python"
    revision = get_head_commit_hash(scan_root) if is_git_repo(scan_root) else ""
    return {
        "project_input": target_value,
        "target": normalized_target,
        "scan_root": str(scan_root),
        "language": resolved_language,
        "is_git_repo": is_git_repo(scan_root),
        "source_kind": source_kind,
        "scan_revision": revision,
    }


def _perform_history_snapshot(
    identity: str,
    commit_hash: str,
    *,
    target_override: str = "",
    language_override: str = "",
) -> dict[str, Any]:
    scan_context = _resolve_history_scan_context(
        identity,
        target_override=target_override,
        language_override=language_override,
    )
    if scan_context is None:
        raise ApiError("graph_not_scanned", "No graph scanned yet", 404)

    snapshot_root: Path | None = None
    try:
        validated_commit_hash = validate_commit_hash(commit_hash)
        repo_root = Path(str(scan_context.get("scan_root") or "")).resolve()
        if not is_git_repo(repo_root):
            raise ApiError("history_requires_git_repo", "Time-travel history requires a git repository.", 400)
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
    target_override = str(payload.get("target") or "").strip()
    language_override = str(payload.get("language") or "").strip().lower()
    return _perform_history_snapshot(
        identity,
        commit_hash,
        target_override=target_override,
        language_override=language_override,
    )


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
    target_override = str(request.args.get("target") or "").strip()
    language_override = str(request.args.get("language") or "").strip().lower()
    scan_context = _resolve_history_scan_context(
        identity,
        target_override=target_override,
        language_override=language_override,
    )
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
    history_graph = _perform_history_snapshot(
        identity,
        commit_hash,
        target_override=str(request.args.get("target") or "").strip(),
        language_override=str(request.args.get("language") or "").strip().lower(),
    )
    return jsonify(history_graph)


def _history_diff_impl(from_commit: str, to_commit: str) -> Any:
    identity = get_request_identity()
    scan_context = _resolve_history_scan_context(
        identity,
        target_override=str(request.args.get("target") or "").strip(),
        language_override=str(request.args.get("language") or "").strip().lower(),
    )
    if scan_context is None:
        return error_response("graph_not_scanned", "No graph scanned yet", 404)

    validated_from = validate_commit_hash(from_commit, field_name="from_commit")
    validated_to = validate_commit_hash(to_commit, field_name="to_commit")
    repo_root = Path(str(scan_context.get("scan_root") or "")).resolve()
    if not is_git_repo(repo_root):
        return error_response("history_diff_requires_git_repo", "Time-travel diff requires a git repository.", 400)

    diff_data = diff_commits(repo_root, validated_from, validated_to)
    return jsonify(diff_data)


def _parse_pr_url(pr_url: str) -> tuple[str, str, str]:
    value = str(pr_url or "").strip()
    match = PR_URL_PATTERN.match(value)
    if not match:
        raise ApiError(
            "invalid_pr_url",
            "PR URL must look like https://github.com/<owner>/<repo>/pull/<number>",
            400,
        )
    owner = match.group("owner").strip()
    repo = match.group("repo").strip()
    number = match.group("number").strip()
    return owner, repo, number


def _fetch_github_pr_refs(owner: str, repo: str, number: str) -> tuple[str, str] | None:
    api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "codeweave-pr-analyzer"}
    token = str(os.getenv("GITHUB_TOKEN", "")).strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request_obj = Request(api_url, headers=headers, method="GET")
    try:
        with urlopen(request_obj, timeout=8) as response:  # nosec: B310 - user-provided URL is normalized to GitHub API domain.
            payload_text = response.read().decode("utf-8")
    except Exception:
        return None
    try:
        import json

        payload = json.loads(payload_text)
    except Exception:
        return None
    base_sha = str(((payload or {}).get("base") or {}).get("sha") or "").strip()
    head_sha = str(((payload or {}).get("head") or {}).get("sha") or "").strip()
    if not base_sha or not head_sha:
        return None
    return base_sha, head_sha


def _guess_changed_files_for_pr(repo_root: Path, pr_url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    owner, repo, number = _parse_pr_url(pr_url)
    refs = _fetch_github_pr_refs(owner, repo, number)
    if refs:
        base_sha, head_sha = refs
        try:
            diff_data = diff_commits(repo_root, base_sha, head_sha, max_files=200)
            return list(diff_data.get("changed_files") or []), {
                "base_commit": base_sha,
                "head_commit": head_sha,
                "source": "github_api",
                "shortstat": diff_data.get("shortstat"),
            }
        except Exception:
            pass

    fallback = run_git_command(repo_root, ["diff", "--name-status", "HEAD~1", "HEAD"], timeout=90)
    if fallback.returncode != 0:
        raise ApiError("pr_diff_unavailable", "Could not compute changed files for this PR.", 400)
    changed_files: list[dict[str, Any]] = []
    for line in (fallback.stdout or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2:
            status = (parts[0] or "M")[0]
            if status == "R" and len(parts) >= 3:
                changed_files.append({"status": "R", "old_path": parts[1], "path": parts[2]})
            else:
                changed_files.append({"status": status, "path": parts[1]})
    return changed_files, {"base_commit": "HEAD~1", "head_commit": "HEAD", "source": "local_fallback"}


def _normalize_file_path(value: str) -> str:
    return str(value or "").replace("\\", "/").lower().lstrip("./")


def _map_changed_files_to_nodes(
    graph_data: dict[str, Any],
    changed_files: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes = list(graph_data.get("nodes") or [])
    normalized_nodes: list[tuple[dict[str, Any], str]] = [
        (node, _normalize_file_path(str(node.get("file") or ""))) for node in nodes
    ]
    impacted: dict[str, dict[str, Any]] = {}
    unmatched_files: list[str] = []
    for change in changed_files:
        change_path = _normalize_file_path(str(change.get("path") or ""))
        if not change_path:
            continue
        matched_any = False
        for node, node_file in normalized_nodes:
            if not node_file:
                continue
            if node_file == change_path or node_file.endswith(f"/{change_path}") or change_path.endswith(f"/{node_file}"):
                node_id = str(node.get("id") or "")
                if not node_id:
                    continue
                matched_any = True
                if node_id not in impacted:
                    impacted[node_id] = {
                        "id": node_id,
                        "name": str(node.get("name") or node_id),
                        "file": str(node.get("file") or ""),
                        "status": str(change.get("status") or "M"),
                        "churn_count": int(node.get("churn_count") or 0),
                        "mutation_status": str(node.get("mutation_status") or "stable"),
                    }
                else:
                    if impacted[node_id]["status"] != "R":
                        impacted[node_id]["status"] = str(change.get("status") or impacted[node_id]["status"])
        if not matched_any:
            unmatched_files.append(change_path)
    impacted_nodes = sorted(
        impacted.values(),
        key=lambda entry: (int(entry.get("churn_count") or 0), str(entry.get("name") or "").lower()),
        reverse=True,
    )
    hotspots = impacted_nodes[:12]
    return {
        "impacted_nodes": impacted_nodes[:120],
        "hotspots": hotspots,
        "unmatched_files": unmatched_files[:80],
    }


@app.get("/health/live")
def health_live() -> Any:
    return jsonify({"status": "ok", "service": "codeweave"})


@app.get("/health/ready")
def health_ready() -> Any:
    db.init_db()
    return jsonify({"status": "ready", "db": str(db.DB_PATH), "schema_version": db.get_schema_version()})


@app.get("/metrics")
def metrics_endpoint() -> Any:
    return app.response_class(
        response=METRICS.to_prometheus_text(),
        status=200,
        mimetype="text/plain; version=0.0.4; charset=utf-8",
    )


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
        target_override = str(payload.get("target") or "").strip()
        language_override = str(payload.get("language") or "").strip().lower()
        job_id = JOBS.submit(
            identity=get_request_identity(),
            job_type="history_snapshot",
            payload={
                "commit_hash": commit_hash,
                "target": target_override,
                "language": language_override,
            },
            handler=_history_snapshot_job_handler,
        )
        return jsonify({"job_id": job_id, "status": "queued"}), 202
    except ApiError as exc:
        return from_api_error(exc)


@app.get("/api/v1/auth/security")
@require_auth
def get_auth_security_tokens() -> Any:
    identity = get_request_identity()
    payload = {"identity": identity, "tokens": build_security_tokens(identity)}
    response = jsonify(payload)
    return issue_security_cookies(response, identity)


@app.post("/api/v1/admin/cleanup")
@require_auth
def run_cleanup() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ApiError("invalid_request_body", "Request body must be a JSON object.", 400)
    try:
        scan_ttl_days = int(payload.get("scan_ttl_days", SCAN_TTL_DAYS))
        job_ttl_days = int(payload.get("job_ttl_days", JOB_TTL_DAYS))
        audit_ttl_days = int(payload.get("audit_ttl_days", AUDIT_TTL_DAYS))
    except (TypeError, ValueError) as exc:
        raise ApiError("invalid_cleanup_ttl", "TTL values must be integers.", 400) from exc
    summary = db.cleanup_expired_artifacts(
        scan_ttl_days=max(1, scan_ttl_days),
        job_ttl_days=max(1, job_ttl_days),
        audit_ttl_days=max(1, audit_ttl_days),
    )
    return jsonify({"ok": True, "cleanup": summary})


@app.get("/api/v1/workspaces")
@require_auth
def list_workspaces_v1() -> Any:
    try:
        workspaces = db.list_workspaces(identity=get_request_identity())
        return jsonify({"workspaces": workspaces})
    except Exception as exc:
        LOGGER.exception("Workspace list failed: %s", exc)
        return error_response("workspace_list_failed", "Could not list workspaces.", 500)


@app.post("/api/v1/workspaces")
@require_auth
def create_workspace_v1() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ApiError("invalid_request_body", "Request body must be a JSON object.", 400)
    try:
        workspace = db.create_workspace(identity=get_request_identity(), name=str(payload.get("name") or ""))
        return jsonify({"workspace": workspace}), 201
    except ValueError as exc:
        raise ApiError("invalid_workspace", str(exc), 400) from exc
    except Exception as exc:
        LOGGER.exception("Workspace create failed: %s", exc)
        return error_response("workspace_create_failed", "Could not create workspace.", 500)


@app.post("/api/v1/workspaces/<int:workspace_id>/members")
@require_auth
def add_workspace_member_v1(workspace_id: int) -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ApiError("invalid_request_body", "Request body must be a JSON object.", 400)
    try:
        member = db.add_workspace_member(
            identity=get_request_identity(),
            workspace_id=workspace_id,
            member_identity=str(payload.get("identity") or ""),
            role=str(payload.get("role") or "member"),
        )
        return jsonify({"member": member})
    except PermissionError as exc:
        raise ApiError("workspace_forbidden", str(exc), 403) from exc
    except ValueError as exc:
        raise ApiError("invalid_workspace_member", str(exc), 400) from exc


@app.post("/api/v1/share-links")
@require_auth
def create_share_link_v1() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ApiError("invalid_request_body", "Request body must be a JSON object.", 400)
    workspace_id_raw = payload.get("workspace_id")
    workspace_id = int(workspace_id_raw) if workspace_id_raw is not None else None
    state_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    ttl_hours = payload.get("expires_hours", SHARE_LINK_TTL_HOURS)
    try:
        ttl_hours_value = max(1, int(ttl_hours))
    except (TypeError, ValueError) as exc:
        raise ApiError("invalid_share_ttl", "expires_hours must be an integer.", 400) from exc
    try:
        share_link = db.create_share_link(
            identity=get_request_identity(),
            payload=state_payload,
            workspace_id=workspace_id,
            expires_hours=ttl_hours_value,
        )
        token = str(share_link.get("token") or "")
        share_url = f"{request.host_url.rstrip('/')}/?share={token}"
        return jsonify({"share_link": {**share_link, "url": share_url}}), 201
    except PermissionError as exc:
        raise ApiError("workspace_forbidden", str(exc), 403) from exc
    except ValueError as exc:
        raise ApiError("invalid_share_payload", str(exc), 400) from exc


@app.get("/api/v1/share-links/<token>")
@require_auth
def resolve_share_link_v1(token: str) -> Any:
    try:
        resolved = db.resolve_share_link(identity=get_request_identity(), token=token)
        if not resolved:
            raise ApiError("share_link_not_found", "Share link not found or expired.", 404)
        return jsonify({"share_link": resolved})
    except PermissionError as exc:
        raise ApiError("workspace_forbidden", str(exc), 403) from exc


@app.get("/api/v1/investigations")
@require_auth
def list_investigations_v1() -> Any:
    workspace_id_raw = request.args.get("workspace_id")
    workspace_id: int | None = None
    if workspace_id_raw:
        try:
            workspace_id = int(workspace_id_raw)
        except ValueError as exc:
            raise ApiError("invalid_workspace_id", "workspace_id must be an integer.", 400) from exc
    investigations = db.list_investigation_sessions(identity=get_request_identity(), workspace_id=workspace_id)
    return jsonify({"sessions": investigations})


@app.post("/api/v1/investigations")
@require_auth
def create_investigation_v1() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ApiError("invalid_request_body", "Request body must be a JSON object.", 400)
    workspace_id_raw = payload.get("workspace_id")
    workspace_id = int(workspace_id_raw) if workspace_id_raw is not None else None
    state_payload = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    ttl_days_raw = payload.get("ttl_days", INVESTIGATION_TTL_DAYS)
    try:
        ttl_days = max(1, int(ttl_days_raw))
    except (TypeError, ValueError) as exc:
        raise ApiError("invalid_session_ttl", "ttl_days must be an integer.", 400) from exc
    try:
        session = db.create_investigation_session(
            identity=get_request_identity(),
            title=str(payload.get("title") or ""),
            notes=str(payload.get("notes") or ""),
            workspace_id=workspace_id,
            state=state_payload,
            ttl_days=ttl_days,
        )
        return jsonify({"session": session}), 201
    except PermissionError as exc:
        raise ApiError("workspace_forbidden", str(exc), 403) from exc
    except ValueError as exc:
        raise ApiError("invalid_session", str(exc), 400) from exc


@app.get("/api/v1/investigations/<int:session_id>")
@require_auth
def get_investigation_v1(session_id: int) -> Any:
    session = db.get_investigation_session(identity=get_request_identity(), session_id=session_id)
    if not session:
        raise ApiError("investigation_not_found", "Investigation session not found.", 404)
    return jsonify({"session": session})


@app.patch("/api/v1/investigations/<int:session_id>")
@require_auth
def update_investigation_v1(session_id: int) -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ApiError("invalid_request_body", "Request body must be a JSON object.", 400)
    state_payload = payload.get("state")
    if state_payload is not None and not isinstance(state_payload, dict):
        raise ApiError("invalid_session_state", "state must be a JSON object.", 400)
    try:
        session = db.update_investigation_session(
            identity=get_request_identity(),
            session_id=session_id,
            title=str(payload.get("title")).strip() if "title" in payload else None,
            notes=str(payload.get("notes")) if "notes" in payload else None,
            state=state_payload,
        )
        if not session:
            raise ApiError("investigation_not_found", "Investigation session not found.", 404)
        return jsonify({"session": session})
    except PermissionError as exc:
        raise ApiError("investigation_forbidden", str(exc), 403) from exc


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


@app.get("/api/v1/action-plan/<node_id>")
@require_auth
def get_action_plan_v1(node_id: str) -> Any:
    return get_action_plan(node_id)


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


@app.post("/api/v1/pr/analyze")
@require_auth
@rate_limit(HISTORY_RATE_LIMIT, RATE_WINDOW_SECONDS)
def analyze_pull_request_v1() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ApiError("invalid_request_body", "Request body must be a JSON object.", 400)
    pr_url = str(payload.get("pr_url") or "").strip()
    if not pr_url:
        raise ApiError("missing_pr_url", "pr_url is required.", 400)

    graph_data, scan_context = _load_latest_scan(get_request_identity())
    if not isinstance(graph_data, dict) or not isinstance(scan_context, dict):
        raise ApiError("graph_not_scanned", "No graph scanned yet", 404)
    repo_root = Path(str(scan_context.get("scan_root") or "")).resolve()
    if not is_git_repo(repo_root):
        raise ApiError("pr_analysis_requires_git_repo", "PR analysis requires a git-backed scan target.", 400)

    try:
        owner, repo, number = _parse_pr_url(pr_url)
        repo_hint = f"{owner}/{repo}#{number}"
        changed_files, diff_meta = _guess_changed_files_for_pr(repo_root, pr_url)
        mapped = _map_changed_files_to_nodes(graph_data, changed_files)
        return jsonify(
            {
                "pr_url": pr_url,
                "repo_hint": repo_hint,
                "changed_files": changed_files,
                "changed_file_count": len(changed_files),
                "impacted_nodes": mapped["impacted_nodes"],
                "hotspots": mapped["hotspots"],
                "unmatched_files": mapped["unmatched_files"],
                "diff_meta": diff_meta,
            }
        )
    except ApiError:
        raise
    except ValueError as exc:
        raise ApiError("pr_analysis_failed", str(exc), 400) from exc
    except Exception as exc:
        LOGGER.exception("PR analysis failed: %s", exc)
        return error_response("pr_analysis_failed", "Could not analyze pull request.", 500)


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
