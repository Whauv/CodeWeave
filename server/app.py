from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from git_tracker import mutation_tracker
from graph import blast_radius, graph_builder


load_dotenv()
logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
CORS(app)

GRAPH_CACHE: dict[str, list[dict[str, Any]]] | None = None


@app.post("/api/scan")
def scan_project() -> Any:
    global GRAPH_CACHE
    try:
        payload = request.get_json(silent=True) or {}
        project_path = payload.get("path")
        if not project_path or not Path(project_path).exists():
            return jsonify({"error": "Invalid project path"}), 400

        graph_data = graph_builder.build_graph(project_path)
        graph_data["nodes"] = mutation_tracker.track_mutations(project_path, graph_data.get("nodes", []))
        GRAPH_CACHE = graph_data
        return jsonify(graph_data)
    except Exception as exc:
        LOGGER.exception("Scan failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/graph")
def get_graph() -> Any:
    try:
        if GRAPH_CACHE is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(GRAPH_CACHE)
    except Exception as exc:
        LOGGER.exception("Graph fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/node/<node_id>")
def get_node(node_id: str) -> Any:
    try:
        if GRAPH_CACHE is None:
            return jsonify({"error": "No graph scanned yet"}), 404

        for node in GRAPH_CACHE.get("nodes", []):
            if node.get("id") == node_id:
                return jsonify(node)
        return jsonify({"error": "Node not found"}), 404
    except Exception as exc:
        LOGGER.exception("Node fetch failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/api/blast/<node_id>")
def get_blast_radius(node_id: str) -> Any:
    try:
        if GRAPH_CACHE is None:
            return jsonify({"error": "No graph scanned yet"}), 404
        return jsonify(blast_radius.compute_blast_radius(GRAPH_CACHE, node_id))
    except Exception as exc:
        LOGGER.exception("Blast radius failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/")
def serve_index() -> Any:
    try:
        return send_from_directory(str(FRONTEND_DIR), "index.html")
    except Exception as exc:
        LOGGER.exception("Failed to serve index: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/<path:asset_path>")
def serve_frontend_asset(asset_path: str) -> Any:
    try:
        return send_from_directory(str(FRONTEND_DIR), asset_path)
    except Exception as exc:
        LOGGER.exception("Failed to serve asset %s: %s", asset_path, exc)
        return jsonify({"error": str(exc)}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
