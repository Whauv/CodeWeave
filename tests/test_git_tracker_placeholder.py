from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
import shutil

from git_tracker import mutation_tracker


class _FakeModifiedFile:
    def __init__(self, path: str) -> None:
        self.new_path = path
        self.old_path = path
        self.added_lines = 3
        self.complexity = 1


class _FakeCommit:
    def __init__(self, commit_hash: str, paths: list[str]) -> None:
        self.hash = commit_hash
        self.modified_files = [_FakeModifiedFile(path) for path in paths]


class _FakeRepository:
    def __init__(self, _repo_path: str) -> None:
        self._commits = [
            _FakeCommit("c1", ["new_file.py", "hotspot.py"]),
            _FakeCommit("c2", ["fresh_recent.py", "hotspot.py"]),
            _FakeCommit("c3", ["fresh_recent.py", "hotspot.py"]),
            _FakeCommit("c4", ["fresh_recent.py", "hotspot.py"]),
            _FakeCommit("c5", ["other_recent.py", "hotspot.py"]),
            _FakeCommit("c6", ["modified_file.py"]),
        ]

    def traverse_commits(self):
        return iter(self._commits)


class GitTrackerPlaceholderTests(unittest.TestCase):
    def test_git_tracker_classifies_recent_and_hotspot_files(self) -> None:
        repo_root = Path.cwd() / "tests_runtime_git_tracker"
        shutil.rmtree(repo_root, ignore_errors=True)
        try:
            repo_root.mkdir(parents=True, exist_ok=True)
            (repo_root / ".git").mkdir()
            nodes = [
                {"id": "1", "name": "new_file", "file": str(repo_root / "new_file.py")},
                {"id": "2", "name": "modified_file", "file": str(repo_root / "modified_file.py")},
                {"id": "3", "name": "hotspot", "file": str(repo_root / "hotspot.py")},
                {"id": "4", "name": "stable", "file": str(repo_root / "stable.py")},
            ]

            with patch.object(mutation_tracker, "Repository", _FakeRepository):
                updated = mutation_tracker.track_mutations(str(repo_root), nodes)
        finally:
            shutil.rmtree(repo_root, ignore_errors=True)

        by_name = {node["name"]: node for node in updated}
        self.assertEqual(by_name["new_file"]["mutation_status"], "new")
        self.assertEqual(by_name["modified_file"]["mutation_status"], "modified")
        self.assertEqual(by_name["hotspot"]["mutation_status"], "hotspot")
        self.assertEqual(by_name["stable"]["mutation_status"], "stable")


if __name__ == "__main__":
    unittest.main()
