from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

try:
    from server.app import app
    from server.state import STATE
except Exception as import_error:
    app = None
    STATE = None
    APP_IMPORT_ERROR = import_error
else:
    APP_IMPORT_ERROR = None


class AppIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if app is None or STATE is None:
            raise unittest.SkipTest(f"Flask app import unavailable in this environment: {APP_IMPORT_ERROR}")

    def setUp(self) -> None:
        self.client = app.test_client()
        STATE.graph_cache = None
        STATE.scan_context = None
        STATE.history_graph_cache.clear()
        self.runtime_root = Path.cwd() / "tests_runtime_integration"
        shutil.rmtree(self.runtime_root, ignore_errors=True)
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.runtime_root, ignore_errors=True)

    def _run_git(self, repo_root: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout or f"git {' '.join(args)} failed")

    def _build_git_typescript_repo(self) -> Path:
        repo_root = self.runtime_root / "sample_repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        self._run_git(repo_root, "init")
        self._run_git(repo_root, "config", "user.email", "tests@example.com")
        self._run_git(repo_root, "config", "user.name", "CodeWeave Tests")

        source_path = repo_root / "main.ts"
        source_path.write_text(
            "function start() { return loadData(); }\n"
            "function loadData() { return 1; }\n",
            encoding="utf-8",
        )
        self._run_git(repo_root, "add", ".")
        self._run_git(repo_root, "commit", "-m", "initial commit")

        source_path.write_text(
            "function start() { return loadData(); }\n"
            "function loadData() { return transformData(); }\n"
            "function transformData() { return 2; }\n",
            encoding="utf-8",
        )
        self._run_git(repo_root, "add", ".")
        self._run_git(repo_root, "commit", "-m", "add transform step")
        return repo_root

    def test_scan_graph_and_node_round_trip(self) -> None:
        repo_root = self._build_git_typescript_repo()
        response = self.client.post("/api/scan", json={"path": str(repo_root), "language": "typescript"})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertGreaterEqual(len(payload["nodes"]), 2)
        self.assertGreaterEqual(len(payload["edges"]), 1)
        self.assertIn("insights", payload)
        self.assertIn("edge_type_breakdown", payload["insights"])

        graph_response = self.client.get("/api/graph")
        self.assertEqual(graph_response.status_code, 200)

        insights_response = self.client.get("/api/insights")
        self.assertEqual(insights_response.status_code, 200)
        self.assertIn("summary", insights_response.get_json())

        node_id = payload["nodes"][0]["id"]
        node_response = self.client.get(f"/api/node/{node_id}")
        self.assertEqual(node_response.status_code, 200)
        self.assertEqual(node_response.get_json()["id"], node_id)

    def test_history_endpoints_return_commit_timeline_and_snapshot(self) -> None:
        repo_root = self._build_git_typescript_repo()
        scan_response = self.client.post("/api/scan", json={"path": str(repo_root), "language": "typescript"})
        self.assertEqual(scan_response.status_code, 200)

        history_response = self.client.get("/api/history")
        self.assertEqual(history_response.status_code, 200)
        history_payload = history_response.get_json()
        self.assertGreaterEqual(len(history_payload["commits"]), 2)

        latest_commit = history_payload["commits"][-1]["hash"]
        snapshot_response = self.client.get(f"/api/history/{latest_commit}")
        self.assertEqual(snapshot_response.status_code, 200)
        snapshot_payload = snapshot_response.get_json()
        self.assertTrue(snapshot_payload["meta"]["history_mode"])
        self.assertEqual(snapshot_payload["meta"]["history_commit"], latest_commit)


if __name__ == "__main__":
    unittest.main()
