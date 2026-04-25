from __future__ import annotations

import shutil
import subprocess
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
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


class ApiLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if app is None or STATE is None:
            raise unittest.SkipTest(f"Flask app import unavailable in this environment: {APP_IMPORT_ERROR}")

    def setUp(self) -> None:
        self.client = app.test_client()
        self.client.environ_base["HTTP_X_CODEWEAVE_USER"] = f"load-{uuid.uuid4().hex}"
        STATE.reset()
        self.runtime_root = Path.cwd() / "tests_runtime_load"
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

    def _build_repo(self) -> Path:
        repo_root = self.runtime_root / "load_repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        self._run_git(repo_root, "init")
        self._run_git(repo_root, "config", "user.email", "tests@example.com")
        self._run_git(repo_root, "config", "user.name", "CodeWeave Tests")
        file_path = repo_root / "main.ts"
        file_path.write_text(
            "export function alpha(){ return beta(); }\n"
            "export function beta(){ return 1; }\n",
            encoding="utf-8",
        )
        self._run_git(repo_root, "add", ".")
        self._run_git(repo_root, "commit", "-m", "init")
        return repo_root

    def test_repeated_graph_reads_under_load(self) -> None:
        repo_root = self._build_repo()
        scan_response = self.client.post("/api/scan", json={"path": str(repo_root), "language": "typescript"})
        self.assertEqual(scan_response.status_code, 200)
        payload = scan_response.get_json()
        self.assertGreaterEqual(len(payload["nodes"]), 1)

        node_id = payload["nodes"][0]["id"]

        def call_graph() -> int:
            return self.client.get("/api/graph").status_code

        def call_node() -> int:
            return self.client.get(f"/api/node/{node_id}").status_code

        with ThreadPoolExecutor(max_workers=8) as executor:
            graph_statuses = list(executor.map(lambda _idx: call_graph(), range(40)))
            node_statuses = list(executor.map(lambda _idx: call_node(), range(40)))

        self.assertTrue(all(code == 200 for code in graph_statuses))
        self.assertTrue(all(code == 200 for code in node_statuses))


if __name__ == "__main__":
    unittest.main()
