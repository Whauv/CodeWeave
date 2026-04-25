# Server

The Flask backend and its support services live here.

- `app.py` wires routes and startup behavior
- `repository_service.py` handles repo resolution, clone caching, and history snapshots
- `chat_service.py` builds graph-aware AI chat context
- `state.py` stores in-memory graph and history state
- `migrate.py` runs schema migrations manually

Security and data model notes:

- Optional CSRF/session protection (env gated) lives in `auth.py`.
- Audit trail events are persisted to `audit_logs`.
- TTL cleanup for scans/jobs/audit logs runs at startup and is exposed at `POST /api/v1/admin/cleanup`.
- Schema migrations are versioned in `/migrations` with startup schema guard support.
