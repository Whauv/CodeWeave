from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parents[1] / "codeweave.db"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              identity TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              target TEXT NOT NULL,
              source_kind TEXT NOT NULL,
              language TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(user_id, target),
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS scans (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              project_id INTEGER NOT NULL,
              graph_json TEXT NOT NULL,
              scan_context_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id),
              FOREIGN KEY(project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              job_type TEXT NOT NULL,
              status TEXT NOT NULL,
              payload_json TEXT,
              result_json TEXT,
              error_message TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS chat_sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              node_id TEXT,
              provider TEXT,
              message TEXT NOT NULL,
              answer TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS graph_metadata (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scan_id INTEGER NOT NULL,
              node_count INTEGER NOT NULL,
              edge_count INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(scan_id) REFERENCES scans(id)
            );
            """
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
