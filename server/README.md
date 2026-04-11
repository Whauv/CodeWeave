# Server

The Flask backend and its support services live here.

- `app.py` wires routes and startup behavior
- `repository_service.py` handles repo resolution, clone caching, and history snapshots
- `chat_service.py` builds graph-aware AI chat context
- `state.py` stores in-memory graph and history state
