from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from server.migrations import get_current_schema_version, run_migrations


DB_PATH = Path(__file__).resolve().parents[1] / "codeweave.db"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema_version() -> int:
    conn = _connect()
    try:
        return get_current_schema_version(conn)
    finally:
        conn.close()


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        schema_version = run_migrations(conn)
        required_version = max(0, int(os.getenv("CODEWEAVE_REQUIRED_SCHEMA_VERSION", "0")))
        current_version = max(schema_version, get_current_schema_version(conn))
        if current_version < required_version:
            raise RuntimeError(
                f"Database schema version {current_version} is below required version {required_version}. "
                "Run migrations before starting the server."
            )
        conn.commit()
    finally:
        conn.close()


def get_or_create_user(identity: str) -> int:
    now = _utc_now()
    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM users WHERE identity = ?", (identity,)).fetchone()
        if row:
            return int(row["id"])
        cursor = conn.execute("INSERT INTO users(identity, created_at) VALUES (?, ?)", (identity, now))
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def upsert_project(user_id: int, target: str, source_kind: str, language: str) -> int:
    now = _utc_now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO projects(user_id, target, source_kind, language, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, target) DO UPDATE SET
              source_kind=excluded.source_kind,
              language=excluded.language,
              updated_at=excluded.updated_at
            """,
            (user_id, target, source_kind, language, now),
        )
        row = conn.execute("SELECT id FROM projects WHERE user_id = ? AND target = ?", (user_id, target)).fetchone()
        conn.commit()
        return int(row["id"])
    finally:
        conn.close()


def save_scan_artifact(
    *,
    identity: str,
    target: str,
    source_kind: str,
    language: str,
    graph_data: dict[str, Any],
    scan_context: dict[str, Any],
) -> int:
    user_id = get_or_create_user(identity)
    project_id = upsert_project(user_id, target, source_kind, language)
    now = _utc_now()
    graph_json = json.dumps(graph_data)
    scan_context_json = json.dumps(scan_context)
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO scans(user_id, project_id, graph_json, scan_context_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, project_id, graph_json, scan_context_json, now),
        )
        scan_id = int(cursor.lastrowid)
        node_count = len(graph_data.get("nodes", []))
        edge_count = len(graph_data.get("edges", []))
        conn.execute(
            "INSERT INTO graph_metadata(scan_id, node_count, edge_count, created_at) VALUES (?, ?, ?, ?)",
            (scan_id, node_count, edge_count, now),
        )
        conn.commit()
        return scan_id
    finally:
        conn.close()


def get_latest_scan_for_identity(identity: str) -> dict[str, Any] | None:
    user_id = get_or_create_user(identity)
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT graph_json, scan_context_json, created_at
            FROM scans
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "graph_data": json.loads(str(row["graph_json"])),
            "scan_context": json.loads(str(row["scan_context_json"])),
            "created_at": str(row["created_at"]),
        }
    finally:
        conn.close()


def save_job(
    *,
    job_id: str,
    identity: str,
    job_type: str,
    status: str,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    user_id = get_or_create_user(identity)
    now = _utc_now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO jobs(id, user_id, job_type, status, payload_json, result_json, error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              status=excluded.status,
              result_json=excluded.result_json,
              error_message=excluded.error_message,
              updated_at=excluded.updated_at
            """,
            (
                job_id,
                user_id,
                job_type,
                status,
                json.dumps(payload) if payload is not None else None,
                json.dumps(result) if result is not None else None,
                error_message,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_job(job_id: str, identity: str) -> dict[str, Any] | None:
    user_id = get_or_create_user(identity)
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT id, job_type, status, payload_json, result_json, error_message, created_at, updated_at
            FROM jobs
            WHERE id = ? AND user_id = ?
            """,
            (job_id, user_id),
        ).fetchone()
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "job_type": str(row["job_type"]),
            "status": str(row["status"]),
            "payload": json.loads(str(row["payload_json"])) if row["payload_json"] else None,
            "result": json.loads(str(row["result_json"])) if row["result_json"] else None,
            "error_message": str(row["error_message"]) if row["error_message"] else None,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
    finally:
        conn.close()


def save_audit_event(
    *,
    identity: str,
    action: str,
    target: str | None = None,
    status_code: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    user_id = get_or_create_user(identity)
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO audit_logs(user_id, action, target, status_code, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                action[:120],
                (target or "")[:400],
                int(status_code) if status_code is not None else None,
                json.dumps(metadata or {}),
                _utc_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_expired_artifacts(
    *,
    scan_ttl_days: int = 30,
    job_ttl_days: int = 14,
    audit_ttl_days: int = 30,
) -> dict[str, int]:
    now = datetime.now(UTC)
    scan_cutoff = now.replace(microsecond=0).isoformat()
    job_cutoff = scan_cutoff
    audit_cutoff = scan_cutoff
    if scan_ttl_days > 0:
        scan_cutoff = (now - timedelta(days=scan_ttl_days)).replace(microsecond=0).isoformat()
    if job_ttl_days > 0:
        job_cutoff = (now - timedelta(days=job_ttl_days)).replace(microsecond=0).isoformat()
    if audit_ttl_days > 0:
        audit_cutoff = (now - timedelta(days=audit_ttl_days)).replace(microsecond=0).isoformat()

    conn = _connect()
    try:
        scans_deleted = (
            conn.execute("DELETE FROM scans WHERE created_at < ?", (scan_cutoff,)).rowcount if scan_ttl_days > 0 else 0
        )
        jobs_deleted = (
            conn.execute("DELETE FROM jobs WHERE updated_at < ?", (job_cutoff,)).rowcount if job_ttl_days > 0 else 0
        )
        audit_deleted = (
            conn.execute("DELETE FROM audit_logs WHERE created_at < ?", (audit_cutoff,)).rowcount
            if audit_ttl_days > 0
            else 0
        )
        conn.commit()
        return {
            "scans_deleted": int(scans_deleted or 0),
            "jobs_deleted": int(jobs_deleted or 0),
            "audit_deleted": int(audit_deleted or 0),
        }
    finally:
        conn.close()
