# AGENTS

## Purpose

CodeWeave is a local-first codebase topology explorer that scans repositories, builds dependency graphs, overlays git mutation data, and serves an interactive browser UI for architecture exploration and impact analysis.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm install
```

## Run Commands

```powershell
python server\app.py
```

Stable server startup:

```powershell
.venv\Scripts\python server\app.py
```

## Test Commands

Python unit and integration coverage:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Browser smoke coverage:

```powershell
npm run smoke
```

## Folder Map

- `frontend/` browser UI modules, rendering, state, interactions, and persistence
- `server/` Flask routes plus chat, repository, and runtime state services
- `parser/` Python AST extraction and summarization helpers
- `graph/` graph assembly and blast-radius logic
- `git_tracker/` git mutation metadata enrichment
- `plugins/` language-specific scanning adapters
- `tests/` Python unit and integration tests
- `smoke/` Playwright browser smoke and interaction tests
- `assets/` screenshots and supporting documentation assets

## Code Style

- Keep Python functions type hinted
- Prefer small, focused modules over new monoliths
- Treat generated runtime folders and caches as non-source artifacts
- Do not commit secrets; add new env vars to `.env.example`
- Preserve existing routes and graph behavior unless explicitly changing product behavior

## Refactor Rules

- Avoid rewriting business logic during structural cleanup
- Prefer additive docs/config over risky source moves
- If a move is unavoidable, use `git mv` to preserve history
- Keep `.github/` documentation-free unless explicitly requested
