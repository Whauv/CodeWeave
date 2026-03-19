# CodeMapper

Interactive Python codebase mapping for architecture exploration, impact analysis, and AI-assisted code understanding.

## What It Does

CodeMapper scans a Python project, extracts classes and functions with the built-in AST, builds a dependency graph, enriches nodes with AI summaries, overlays recent Git mutation data, and serves an interactive graph UI with blast-radius analysis.

## Core Features

- AI-powered node summaries with Groq, local summary caching, and safe fallback behavior when no API key is available
- Git mutation tracking with PyDriller to mark stable files, recent changes, modified files, and hotspots
- Interactive graph exploration with D3.js, Monaco source viewing, node search, detail drill-down, and blast radius simulation
- Reverse-BFS blast radius analysis to estimate downstream impact before changing a function or class

## UI Features

- Tree and force layout modes for exploring the codebase from different angles
- Light and dark themes
- Node hover summaries for one-line context
- Saved chat threads for node-level conversations
- Recent scan history with per-item delete controls
- Cached graph snapshots so previously scanned projects can be reopened without rescanning
- Split view, graph focus, prompt focus, and a draggable divider between panes

## Performance Notes

- Summary generation is batched to reduce Groq rate-limit pressure and speed up large scans
- Large non-project directories such as `.venv`, `node_modules`, `site-packages`, and `.git` are skipped during scanning
- Graph panning, zooming, and divider resizing are optimized to reduce lag during exploration
- Force-layout rendering uses lighter-weight edge updates to keep movement smoother on dense graphs

## Setup

1. Clone the repo.
2. Install dependencies:
   `pip install -r requirements.txt`
3. Add your `GROQ_API_KEY` to `.env` after creating a free key at [console.groq.com](https://console.groq.com).
4. Start the server:
   `python server/app.py`
5. Open [http://localhost:5050](http://localhost:5050).

## Usage

- Enter an absolute local project path and click `Scan Project`
- Click a node to inspect details, callers, callees, chat context, and mutation metadata
- Use `Simulate Blast Radius` or right-click a node to run impact analysis
- Double-click a node to open its source in Monaco Editor
- Use the search bar to filter nodes by name or summary
- Reopen recent scans or saved chats without rescanning when cached graph data is available

## Project Structure

```text
codemapper/
├── parser/
├── git_tracker/
├── graph/
├── server/
├── frontend/
├── .env
├── summaries_cache.json
├── requirements.txt
└── README.md
```

## Screenshots

_Add screenshots here._

## License

MIT
