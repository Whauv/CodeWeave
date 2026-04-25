from __future__ import annotations

from server import db


def main() -> int:
    db.init_db()
    version = db.get_schema_version()
    print(f"CodeWeave schema version: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
