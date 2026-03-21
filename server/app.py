from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from groq import Groq

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


load_dotenv()
logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
CORS(app)

GRAPH_CACHE: dict[str, Any] | None = None
SCAN_CONTEXT: dict[str, Any] | None = None
HISTORY_GRAPH_CACHE: dict[str, dict[str, Any]] = {}
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


def _clone_github_repo(repo_url: str, target_dir: Path, include_all_branches: bool = False) -> Path:
    try:
        clone_args = ["git", "clone", "--depth", "1"]
        if not include_all_branches:
            clone_args.append("--single-branch")
        clone_args.extend([repo_url, str(target_dir)])
        result = subprocess.run(clone_args, capture_output=True, text=True, check=False, timeout=300)
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


def _fetch_all_remote_branches(repo_root: Path, depth: int = 160) -> subprocess.CompletedProcess[str]:
    return _run_git_command(
        repo_root,
        [
            "fetch",
            "--prune",
            "--tags",
            "--depth",
            str(depth),
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
        ],
        timeout=300,
    )


def _ensure_cached_repo(repo_url: str, include_all_branches: bool = False) -> Path:
    repo_hash = hashlib.md5(repo_url.encode("utf-8")).hexdigest()[:12]
    cache_root = Path(tempfile.gettempdir()) / "codeweave_repo_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    target_dir = cache_root / repo_hash

    if target_dir.exists() and (target_dir / ".git").exists():
        if include_all_branches:
            result = _fetch_all_remote_branches(target_dir)
        else:
            result = subprocess.run(
                ["git", "-C", str(target_dir), "fetch", "--depth", "40", "origin"],
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
        if result.returncode != 0:
            LOGGER.warning("Failed to refresh cached repo %s: %s", repo_url, result.stderr or result.stdout)
        return target_dir

    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    return _clone_github_repo(repo_url, target_dir, include_all_branches=include_all_branches)


def _resolve_scan_source(project_input: str, include_all_branches: bool = False) -> tuple[Path, str, str]:
    if project_input.startswith(("http://", "https://")):
        repo_url = _normalize_github_repo_url(project_input)
        clone_path = _ensure_cached_repo(repo_url, include_all_branches=include_all_branches)
        return clone_path, repo_url, "github"
    resolved_path = Path(project_input).expanduser().resolve()
    if not resolved_path.exists() or not resolved_path.is_dir():
        raise ValueError("Invalid project path")
    return resolved_path, str(resolved_path), "local"


def _is_git_repo(repo_root: Path) -> bool:
    return repo_root.exists() and (repo_root / ".git").exists()


def _run_git_command(
    repo_root: Path,
    args: list[str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _get_commit_count(repo_root: Path) -> int:
    result = _run_git_command(repo_root, ["rev-list", "--count", "--all"], timeout=60)
    if result.returncode != 0:
        return 0
    try:
        return int((result.stdout or "0").strip())
    except ValueError:
        return 0


def _get_head_branch(repo_root: Path) -> str:
    result = _run_git_command(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=60)
    if result.returncode != 0:
        return "HEAD"
    return (result.stdout or "HEAD").strip()


def _is_shallow_repository(repo_root: Path) -> bool:
    result = _run_git_command(repo_root, ["rev-parse", "--is-shallow-repository"], timeout=60)
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip().lower() == "true"


def _ensure_repo_history(repo_root: Path, desired_commits: int = 40) -> dict[str, Any]:
    before_count = _get_commit_count(repo_root)
    is_shallow = _is_shallow_repository(repo_root)
    fetched = False
    fetch_error = ""
    head_branch = _get_head_branch(repo_root)

    if is_shallow and before_count < desired_commits:
        deepen_by = max(desired_commits * 2, 120)
        fetch_result = _run_git_command(
            repo_root,
            ["fetch", "--unshallow", "--tags", "origin"],
            timeout=240,
        )
        fetched = fetch_result.returncode == 0
        if fetch_result.returncode != 0:
            fallback_result = _run_git_command(
                repo_root,
                ["fetch", "--deepen", str(deepen_by), "--tags", "origin", head_branch],
                timeout=240,
            )
            fetched = fallback_result.returncode == 0
            if fallback_result.returncode != 0:
                fetch_error = (fallback_result.stderr or fallback_result.stdout or fetch_result.stderr or fetch_result.stdout or "").strip()
                LOGGER.warning("Failed to deepen git history for %s: %s", repo_root, fetch_error)

    after_count = _get_commit_count(repo_root)
    return {
        "before_count": before_count,
        "after_count": after_count,
        "is_shallow": _is_shallow_repository(repo_root),
        "attempted_fetch": is_shallow and before_count < desired_commits,
        "fetched": fetched,
        "fetch_error": fetch_error,
        "head_branch": head_branch,
    }


def _list_remote_branch_names(repo_root: Path) -> list[str]:
    result = _run_git_command(
        repo_root,
        ["for-each-ref", "refs/remotes/origin", "--format=%(refname:short)"],
        timeout=60,
    )
    if result.returncode != 0:
        return []
    branches = [
        line.strip().replace("origin/", "", 1)
        for line in (result.stdout or "").splitlines()
        if line.strip() and line.strip() != "origin/HEAD"
    ]
    return sorted(set(branches))


def _list_repo_commits(repo_root: Path, limit: int = 40) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not _is_git_repo(repo_root):
        raise ValueError("Time-travel requires a git repository.")

    history_meta = _ensure_repo_history(repo_root, desired_commits=limit)

    result = _run_git_command(
        repo_root,
        [
            "log",
            f"--max-count={limit}",
            "--all",
            "--date-order",
            "--reverse",
            "--date=short",
            "--pretty=format:%H%x1f%h%x1f%ad%x1f%an%x1f%s",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "Failed to read git history")

    commits: list[dict[str, str]] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        full_hash, short_hash, date_value, author, subject = parts
        commits.append(
            {
                "hash": full_hash,
                "short_hash": short_hash,
                "date": date_value,
                "author": author,
                "message": subject,
            }
        )
    history_meta["returned_count"] = len(commits)
    history_meta["branch_names"] = _list_remote_branch_names(repo_root)
    return commits, history_meta


def _extract_commit_snapshot(repo_root: Path, commit_hash: str) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="codeweave_history_"))
    archive = subprocess.run(
        ["git", "-C", str(repo_root), "archive", "--format=tar", commit_hash],
        capture_output=True,
        check=False,
        timeout=240,
    )
    if archive.returncode != 0:
        raise ValueError("Failed to export commit snapshot")

    tar_path = temp_dir / "snapshot.tar"
    tar_path.write_bytes(archive.stdout)
    with tarfile.open(tar_path) as tar_file:
        tar_file.extractall(temp_dir / "repo")
    tar_path.unlink(missing_ok=True)
    return temp_dir / "repo"


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
    global GRAPH_CACHE, SCAN_CONTEXT
    try:
        payload = request.get_json(silent=True) or {}
        project_input = str(payload.get("path") or "").strip()
        language = str(payload.get("language") or "python").strip().lower()
        if not project_input:
            return jsonify({"error": "Invalid project path"}), 400

        scan_root, normalized_target, source_kind = _resolve_scan_source(project_input)
        graph_data = _scan_repository(scan_root, language)

        GRAPH_CACHE = graph_data
        SCAN_CONTEXT = {
            "project_input": project_input,
            "target": normalized_target,
            "scan_root": str(scan_root),
            "language": language,
            "is_git_repo": _is_git_repo(scan_root),
            "source_kind": source_kind,
        }
        HISTORY_GRAPH_CACHE.clear()
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


@app.get("/api/history")
def get_time_travel_commits() -> Any:
    try:
        if SCAN_CONTEXT is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        source_kind = str(SCAN_CONTEXT.get("source_kind") or "local")
        repo_root = Path(str(SCAN_CONTEXT.get("scan_root") or "")).resolve()
        if source_kind == "github":
            repo_url = str(SCAN_CONTEXT.get("target") or "").strip()
            if repo_url:
                repo_root = _ensure_cached_repo(repo_url, include_all_branches=True)
        commits, history_meta = _list_repo_commits(repo_root)
        return jsonify(
            {
                "target": SCAN_CONTEXT.get("target"),
                "language": SCAN_CONTEXT.get("language"),
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
    try:
        if SCAN_CONTEXT is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        repo_root = Path(str(SCAN_CONTEXT.get("scan_root") or "")).resolve()
        language = str(SCAN_CONTEXT.get("language") or "python")
        cache_key = f"{repo_root}::{language}::{commit_hash}"
        if cache_key in HISTORY_GRAPH_CACHE:
            return jsonify(HISTORY_GRAPH_CACHE[cache_key])

        snapshot_root = _extract_commit_snapshot(repo_root, commit_hash)
        graph_data = _scan_repository(
            snapshot_root,
            language,
            include_summaries=False,
            include_mutation_tracking=False,
        )
        graph_data.setdefault("meta", {})
        graph_data["meta"].update(
            {
                "history_commit": commit_hash,
                "history_mode": True,
            }
        )
        HISTORY_GRAPH_CACHE[cache_key] = graph_data
        return jsonify(graph_data)
    except Exception as exc:
        LOGGER.exception("History graph fetch failed: %s", exc)
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
    debug_enabled = os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=5050, debug=debug_enabled, use_reloader=False)
