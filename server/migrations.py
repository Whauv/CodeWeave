from __future__ import annotations

from pathlib import Path
import sqlite3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _parse_migration_version(path: Path) -> int:
    prefix = path.name.split("_", 1)[0]
    return int(prefix)


def discover_migrations() -> list[Path]:
    if not MIGRATIONS_DIR.exists():
        return []
    files = [
        path
        for path in MIGRATIONS_DIR.glob("*.sql")
        if path.is_file() and path.name.split("_", 1)[0].isdigit()
    ]
    return sorted(files, key=_parse_migration_version)


def get_current_schema_version(conn: sqlite3.Connection) -> int:
    _ensure_migrations_table(conn)
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
    return int(row["version"] if row else 0)


def run_migrations(conn: sqlite3.Connection) -> int:
    _ensure_migrations_table(conn)
    current_version = get_current_schema_version(conn)
    applied_version = current_version
    for migration_path in discover_migrations():
        version = _parse_migration_version(migration_path)
        if version <= current_version:
            continue
        sql = migration_path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
            (version, migration_path.name),
        )
        applied_version = version
    conn.commit()
    return applied_version

