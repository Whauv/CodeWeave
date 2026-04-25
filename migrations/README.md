# Migrations

This folder stores SQL migrations for the SQLite production schema.

- `0001_initial.sql` creates users, projects, scans, jobs, chat_sessions, and graph_metadata.
- `0002_wave3_security_data_model.sql` adds workspaces + audit logs and performance indexes.

## Runner

Migrations are applied automatically on server startup via `server.db.init_db()`.

You can also run them manually:

```powershell
.venv\Scripts\python.exe -m server.migrate
```

Optional schema guard:

- Set `CODEWEAVE_REQUIRED_SCHEMA_VERSION=<n>` to force startup failure if DB is behind.
