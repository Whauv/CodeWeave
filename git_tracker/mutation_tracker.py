from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    from pydriller import Repository
except Exception:
    Repository = None


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def _mark_nodes_stable(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for node in nodes:
        node.setdefault("mutation_status", "stable")
        node.setdefault("mutation_color", "#aaaaaa")
        node.setdefault("churn_count", 0)
        node.setdefault("last_modified_commit", None)
    return nodes


def _normalize_path(path_value: str | None, repo_root: Path) -> str:
    if not path_value:
        return ""

    candidate = Path(path_value)
    if candidate.is_absolute():
        return str(candidate.resolve())
    return str((repo_root / candidate).resolve())


def track_mutations(repo_path: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repo_root = Path(repo_path).resolve()
    if Repository is None:
        LOGGER.warning("PyDriller is unavailable; marking nodes as stable.")
        return _mark_nodes_stable(nodes)

    if not repo_root.exists() or not (repo_root / ".git").exists():
        LOGGER.warning("Path is not a git repository: %s", repo_path)
        return _mark_nodes_stable(nodes)

    file_map: dict[str, dict[str, Any]] = {}
    recent_files: list[str] = []

    try:
        for commit_index, commit in enumerate(Repository(str(repo_root)).traverse_commits()):
            if commit_index >= 30:
                break

            for modified_file in commit.modified_files:
                file_path = modified_file.new_path or modified_file.old_path
                if not file_path or not file_path.endswith(".py"):
                    continue

                normalized_path = _normalize_path(file_path, repo_root)
                recent_files.append(normalized_path)
                current = file_map.setdefault(
                    normalized_path,
                    {
                        "churn_count": 0,
                        "last_commit": commit.hash,
                        "added_lines": 0,
                        "complexity": 0,
                        "last_seen_index": commit_index,
                    },
                )
                current["churn_count"] += 1
                current["last_commit"] = current.get("last_commit") or commit.hash
                current["added_lines"] += getattr(modified_file, "added_lines", 0) or 0
                current["complexity"] = getattr(modified_file, "complexity", 0) or 0
                current["last_seen_index"] = min(current.get("last_seen_index", commit_index), commit_index)
    except Exception as exc:
        LOGGER.warning("Unable to inspect git history for %s: %s", repo_path, exc)
        return _mark_nodes_stable(nodes)

    if not file_map:
        return _mark_nodes_stable(nodes)

    recent_five_files = set(recent_files[:5])
    recent_thirty_files = set(recent_files[:30])

    for node in nodes:
        node_path = _normalize_path(node.get("file"), repo_root)
        metadata = file_map.get(node_path)
        if metadata is None:
            node["mutation_status"] = "stable"
            node["mutation_color"] = "#aaaaaa"
            node["churn_count"] = 0
            node["last_modified_commit"] = None
            continue

        churn_count = int(metadata.get("churn_count", 0))
        if churn_count >= 5:
            mutation_status = "hotspot"
            mutation_color = "#ff4444"
        elif node_path in recent_five_files:
            mutation_status = "new"
            mutation_color = "#00ff88"
        elif node_path in recent_thirty_files:
            mutation_status = "modified"
            mutation_color = "#ffcc00"
        else:
            mutation_status = "stable"
            mutation_color = "#aaaaaa"

        node["mutation_status"] = mutation_status
        node["mutation_color"] = mutation_color
        node["churn_count"] = churn_count
        node["last_modified_commit"] = metadata.get("last_commit")

    return nodes


if __name__ == "__main__":
    sample_nodes = [
        {"id": "1", "name": "demo", "file": str(Path.cwd() / "mutation_tracker.py")},
    ]
    print(track_mutations(str(Path.cwd()), sample_nodes))
