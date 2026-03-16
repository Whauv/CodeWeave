# CodeMapper

Interactive Python codebase mapper with AI summaries, Git mutation tracking, graph exploration, and blast radius simulation.

## Features

- AI-powered node summaries with Groq and cached fallback behavior
- Live Git mutation tracking across recent commits with PyDriller
- Interactive node-based web UI built with Flask, D3.js, and Monaco Editor
- Blast radius simulation using reverse BFS on a directed dependency graph

## Setup

1. Clone the repo.
2. Run `pip install -r requirements.txt`.
3. Add your `GROQ_API_KEY` to `.env` after creating a free key at [console.groq.com](https://console.groq.com).
4. Run `python server/app.py`.
5. Open [http://localhost:5050](http://localhost:5050).

## Usage

- Enter an absolute project path and click `Scan`.
- Click any node to inspect its details.
- Right-click any node to simulate blast radius.
- Double-click any node to open its source in Monaco Editor.
- Use the search bar to filter nodes by name or summary.

## Screenshots

_Add screenshots here._

## License

MIT
