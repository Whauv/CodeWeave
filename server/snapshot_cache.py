from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".codeweave_tmp" / "snapshot_cache"


def _cache_root() -> Path:
    raw = os.getenv("CODEWEAVE_SNAPSHOT_CACHE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_CACHE_DIR


def _safe_key(*parts: str) -> str:
    joined = "::".join(part.strip().lower() for part in parts if part and part.strip())
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def _scan_cache_path(*, target: str, language: str, revision: str, source_kind: str) -> Path:
    key = _safe_key(target, language, revision, source_kind, "scan")
    return _cache_root() / "scan" / f"{key}.json"


def _history_cache_path(*, repo_root: Path, commit_hash: str, language: str) -> Path:
    key = _safe_key(str(repo_root.resolve()), commit_hash, language, "history")
    return _cache_root() / "history" / f"{key}.json"


def load_scan_snapshot(*, target: str, language: str, revision: str, source_kind: str) -> dict[str, Any] | None:
    return _read_json(
        _scan_cache_path(target=target, language=language, revision=revision, source_kind=source_kind)
    )


def save_scan_snapshot(
    *,
    target: str,
    language: str,
    revision: str,
    source_kind: str,
    graph_data: dict[str, Any],
) -> None:
    _write_json(
        _scan_cache_path(target=target, language=language, revision=revision, source_kind=source_kind),
        graph_data,
    )


def load_history_snapshot(*, repo_root: Path, commit_hash: str, language: str) -> dict[str, Any] | None:
    return _read_json(_history_cache_path(repo_root=repo_root, commit_hash=commit_hash, language=language))


def save_history_snapshot(
    *,
    repo_root: Path,
    commit_hash: str,
    language: str,
    graph_data: dict[str, Any],
) -> None:
    _write_json(
        _history_cache_path(repo_root=repo_root, commit_hash=commit_hash, language=language),
        graph_data,
    )

