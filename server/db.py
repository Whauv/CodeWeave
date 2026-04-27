from __future__ import annotations

import json
import os
import sqlite3
from secrets import token_urlsafe
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
        conn.execute("INSERT OR IGNORE INTO users(identity, created_at) VALUES (?, ?)", (identity, now))
        row = conn.execute("SELECT id FROM users WHERE identity = ?", (identity,)).fetchone()
        if row:
            conn.commit()
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
        expired_share_links = conn.execute(
            "DELETE FROM share_links WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now.replace(microsecond=0).isoformat(),),
        ).rowcount
        expired_sessions = conn.execute(
            "DELETE FROM investigation_sessions WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now.replace(microsecond=0).isoformat(),),
        ).rowcount
        conn.commit()
        return {
            "scans_deleted": int(scans_deleted or 0),
            "jobs_deleted": int(jobs_deleted or 0),
            "audit_deleted": int(audit_deleted or 0),
            "share_links_deleted": int(expired_share_links or 0),
            "investigations_deleted": int(expired_sessions or 0),
        }
    finally:
        conn.close()


def _workspace_member_count(conn: sqlite3.Connection, workspace_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM workspace_members WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    return int(row["count"] if row else 0)


def _serialize_workspace_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    workspace_id = int(row["id"])
    return {
        "id": workspace_id,
        "name": str(row["name"]),
        "owner_user_id": int(row["user_id"]),
        "role": str(row["role"] or "member"),
        "member_count": _workspace_member_count(conn, workspace_id),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def create_workspace(*, identity: str, name: str) -> dict[str, Any]:
    user_id = get_or_create_user(identity)
    clean_name = " ".join(str(name or "").split()).strip()
    if not clean_name:
        raise ValueError("Workspace name is required.")
    now = _utc_now()
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO workspaces(user_id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, name) DO UPDATE SET
              updated_at=excluded.updated_at
            """,
            (user_id, clean_name[:120], now, now),
        )
        workspace_id = int(cursor.lastrowid or 0)
        if workspace_id == 0:
            row = conn.execute(
                "SELECT id FROM workspaces WHERE user_id = ? AND name = ?",
                (user_id, clean_name[:120]),
            ).fetchone()
            workspace_id = int(row["id"])
        conn.execute(
            """
            INSERT INTO workspace_members(workspace_id, user_id, role, created_at)
            VALUES (?, ?, 'owner', ?)
            ON CONFLICT(workspace_id, user_id) DO UPDATE SET role='owner'
            """,
            (workspace_id, user_id, now),
        )
        row = conn.execute(
            """
            SELECT w.id, w.user_id, w.name, w.created_at, w.updated_at, wm.role
            FROM workspaces w
            JOIN workspace_members wm ON wm.workspace_id = w.id AND wm.user_id = ?
            WHERE w.id = ?
            """,
            (user_id, workspace_id),
        ).fetchone()
        conn.commit()
        if not row:
            raise ValueError("Could not create workspace.")
        return _serialize_workspace_row(conn, row)
    finally:
        conn.close()


def list_workspaces(*, identity: str) -> list[dict[str, Any]]:
    user_id = get_or_create_user(identity)
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT w.id, w.user_id, w.name, w.created_at, w.updated_at, wm.role
            FROM workspaces w
            JOIN workspace_members wm ON wm.workspace_id = w.id
            WHERE wm.user_id = ?
            ORDER BY w.updated_at DESC, w.id DESC
            """,
            (user_id,),
        ).fetchall()
        return [_serialize_workspace_row(conn, row) for row in rows]
    finally:
        conn.close()


def _get_workspace_membership(
    conn: sqlite3.Connection,
    *,
    workspace_id: int,
    user_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT w.id, w.user_id, w.name, w.created_at, w.updated_at, wm.role
        FROM workspaces w
        JOIN workspace_members wm ON wm.workspace_id = w.id
        WHERE w.id = ? AND wm.user_id = ?
        """,
        (workspace_id, user_id),
    ).fetchone()


def add_workspace_member(
    *,
    identity: str,
    workspace_id: int,
    member_identity: str,
    role: str = "member",
) -> dict[str, Any]:
    owner_id = get_or_create_user(identity)
    target_identity = str(member_identity or "").strip()
    if not target_identity:
        raise ValueError("Member identity is required.")
    member_user_id = get_or_create_user(target_identity)
    normalized_role = "owner" if str(role).strip().lower() == "owner" else "member"
    now = _utc_now()
    conn = _connect()
    try:
        workspace = conn.execute(
            "SELECT id, user_id FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if not workspace:
            raise ValueError("Workspace not found.")
        if int(workspace["user_id"]) != owner_id:
            raise PermissionError("Only workspace owner can add members.")
        conn.execute(
            """
            INSERT INTO workspace_members(workspace_id, user_id, role, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(workspace_id, user_id) DO UPDATE SET role=excluded.role
            """,
            (workspace_id, member_user_id, normalized_role, now),
        )
        conn.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?", (now, workspace_id))
        row = conn.execute(
            "SELECT id, identity, created_at FROM users WHERE id = ?",
            (member_user_id,),
        ).fetchone()
        conn.commit()
        return {
            "workspace_id": int(workspace_id),
            "user_id": int(member_user_id),
            "identity": str(row["identity"]) if row else target_identity,
            "role": normalized_role,
        }
    finally:
        conn.close()


def _assert_workspace_member(conn: sqlite3.Connection, *, workspace_id: int, user_id: int) -> None:
    membership = _get_workspace_membership(conn, workspace_id=workspace_id, user_id=user_id)
    if not membership:
        raise PermissionError("You are not a member of this workspace.")


def create_share_link(
    *,
    identity: str,
    payload: dict[str, Any],
    workspace_id: int | None = None,
    expires_hours: int = 168,
) -> dict[str, Any]:
    user_id = get_or_create_user(identity)
    now = datetime.now(UTC)
    expires_at = (now + timedelta(hours=max(1, expires_hours))).replace(microsecond=0).isoformat()
    token = token_urlsafe(18)
    conn = _connect()
    try:
        if workspace_id is not None:
            _assert_workspace_member(conn, workspace_id=int(workspace_id), user_id=user_id)
        cursor = conn.execute(
            """
            INSERT INTO share_links(token, workspace_id, owner_user_id, payload_json, created_at, expires_at, resolved_count)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (
                token,
                int(workspace_id) if workspace_id is not None else None,
                user_id,
                json.dumps(payload or {}),
                now.replace(microsecond=0).isoformat(),
                expires_at,
            ),
        )
        conn.commit()
        return {
            "id": int(cursor.lastrowid),
            "token": token,
            "workspace_id": int(workspace_id) if workspace_id is not None else None,
            "expires_at": expires_at,
        }
    finally:
        conn.close()


def resolve_share_link(*, identity: str, token: str) -> dict[str, Any] | None:
    user_id = get_or_create_user(identity)
    clean_token = str(token or "").strip()
    if not clean_token:
        return None
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT id, token, workspace_id, owner_user_id, payload_json, created_at, expires_at, resolved_count
            FROM share_links
            WHERE token = ?
            """,
            (clean_token,),
        ).fetchone()
        if not row:
            return None
        expires_at = str(row["expires_at"] or "")
        if expires_at and expires_at < now:
            return None
        workspace_id = int(row["workspace_id"]) if row["workspace_id"] is not None else None
        if workspace_id is not None:
            _assert_workspace_member(conn, workspace_id=workspace_id, user_id=user_id)
        conn.execute(
            """
            UPDATE share_links
            SET resolved_count = COALESCE(resolved_count, 0) + 1, last_resolved_at = ?
            WHERE id = ?
            """,
            (now, int(row["id"])),
        )
        conn.commit()
        return {
            "id": int(row["id"]),
            "token": str(row["token"]),
            "workspace_id": workspace_id,
            "owner_user_id": int(row["owner_user_id"]),
            "payload": json.loads(str(row["payload_json"] or "{}")),
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"] or ""),
            "resolved_count": int(row["resolved_count"] or 0) + 1,
        }
    finally:
        conn.close()


def create_investigation_session(
    *,
    identity: str,
    title: str,
    state: dict[str, Any],
    notes: str = "",
    workspace_id: int | None = None,
    ttl_days: int = 30,
) -> dict[str, Any]:
    user_id = get_or_create_user(identity)
    clean_title = " ".join(str(title or "").split()).strip()
    if not clean_title:
        raise ValueError("Session title is required.")
    now = datetime.now(UTC).replace(microsecond=0)
    expires_at = (now + timedelta(days=max(1, ttl_days))).isoformat()
    conn = _connect()
    try:
        if workspace_id is not None:
            _assert_workspace_member(conn, workspace_id=int(workspace_id), user_id=user_id)
        cursor = conn.execute(
            """
            INSERT INTO investigation_sessions(workspace_id, owner_user_id, title, notes, state_json, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(workspace_id) if workspace_id is not None else None,
                user_id,
                clean_title[:160],
                str(notes or "")[:1000],
                json.dumps(state or {}),
                now.isoformat(),
                now.isoformat(),
                expires_at,
            ),
        )
        conn.commit()
        return get_investigation_session(identity=identity, session_id=int(cursor.lastrowid)) or {}
    finally:
        conn.close()


def list_investigation_sessions(
    *,
    identity: str,
    workspace_id: int | None = None,
) -> list[dict[str, Any]]:
    user_id = get_or_create_user(identity)
    conn = _connect()
    try:
        query = """
            SELECT i.id, i.workspace_id, i.owner_user_id, i.title, i.notes, i.state_json, i.created_at, i.updated_at, i.expires_at
            FROM investigation_sessions i
            LEFT JOIN workspace_members wm ON wm.workspace_id = i.workspace_id
            WHERE (i.owner_user_id = ? OR wm.user_id = ?)
        """
        args: list[Any] = [user_id, user_id]
        if workspace_id is not None:
            query += " AND i.workspace_id = ?"
            args.append(int(workspace_id))
        query += " ORDER BY i.updated_at DESC, i.id DESC LIMIT 80"
        rows = conn.execute(query, tuple(args)).fetchall()
        sessions: list[dict[str, Any]] = []
        for row in rows:
            sessions.append(
                {
                    "id": int(row["id"]),
                    "workspace_id": int(row["workspace_id"]) if row["workspace_id"] is not None else None,
                    "owner_user_id": int(row["owner_user_id"]),
                    "title": str(row["title"]),
                    "notes": str(row["notes"] or ""),
                    "state": json.loads(str(row["state_json"] or "{}")),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "expires_at": str(row["expires_at"] or ""),
                }
            )
        return sessions
    finally:
        conn.close()


def get_investigation_session(*, identity: str, session_id: int) -> dict[str, Any] | None:
    user_id = get_or_create_user(identity)
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT i.id, i.workspace_id, i.owner_user_id, i.title, i.notes, i.state_json, i.created_at, i.updated_at, i.expires_at
            FROM investigation_sessions i
            LEFT JOIN workspace_members wm ON wm.workspace_id = i.workspace_id
            WHERE i.id = ? AND (i.owner_user_id = ? OR wm.user_id = ?)
            LIMIT 1
            """,
            (int(session_id), user_id, user_id),
        ).fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "workspace_id": int(row["workspace_id"]) if row["workspace_id"] is not None else None,
            "owner_user_id": int(row["owner_user_id"]),
            "title": str(row["title"]),
            "notes": str(row["notes"] or ""),
            "state": json.loads(str(row["state_json"] or "{}")),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "expires_at": str(row["expires_at"] or ""),
        }
    finally:
        conn.close()


def update_investigation_session(
    *,
    identity: str,
    session_id: int,
    title: str | None = None,
    notes: str | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    user_id = get_or_create_user(identity)
    now = _utc_now()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, owner_user_id, title, notes, state_json FROM investigation_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
        if not row:
            return None
        if int(row["owner_user_id"]) != user_id:
            raise PermissionError("Only the session owner can edit this investigation.")
        next_title = str(title).strip()[:160] if title is not None else str(row["title"])
        if not next_title:
            next_title = "Untitled investigation"
        next_notes = str(notes if notes is not None else row["notes"] or "")[:1000]
        next_state = state if state is not None else json.loads(str(row["state_json"] or "{}"))
        conn.execute(
            """
            UPDATE investigation_sessions
            SET title = ?, notes = ?, state_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_title, next_notes, json.dumps(next_state or {}), now, int(session_id)),
        )
        conn.commit()
        return get_investigation_session(identity=identity, session_id=int(session_id))
    finally:
        conn.close()
