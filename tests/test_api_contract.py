from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

try:
    from server.app import app
    from server.state import STATE
except Exception as import_error:
    app = None
    STATE = None
    APP_IMPORT_ERROR = import_error
else:
    APP_IMPORT_ERROR = None


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if app is None or STATE is None:
            raise unittest.SkipTest(f"Flask app import unavailable in this environment: {APP_IMPORT_ERROR}")

    def setUp(self) -> None:
        self.client = app.test_client()
        self.client.environ_base["HTTP_X_CODEWEAVE_USER"] = f"contract-{uuid.uuid4().hex}"
        STATE.reset()

    def test_scan_contract_v1_and_legacy(self) -> None:
        fake_graph = {
            "nodes": [{"id": "n1", "name": "entry", "file": "main.py"}],
            "edges": [],
            "insights": {"summary": "ok"},
            "meta": {"incremental_reused": False},
        }
        with patch("server.app._perform_scan", return_value=fake_graph):
            v1 = self.client.post("/api/v1/scan", json={"path": ".", "language": "python"})
            legacy = self.client.post("/api/scan", json={"path": ".", "language": "python"})
        self.assertEqual(v1.status_code, 200)
        self.assertEqual(legacy.status_code, 200)
        v1_payload = v1.get_json()
        legacy_payload = legacy.get_json()
        self.assertEqual(set(v1_payload.keys()), set(legacy_payload.keys()))
        self.assertIn("nodes", v1_payload)
        self.assertIn("edges", v1_payload)
        self.assertIn("insights", v1_payload)

    def test_history_contract_v1_and_legacy(self) -> None:
        fake_history = {
            "target": ".",
            "language": "python",
            "source_kind": "local",
            "commits": [{"hash": "1234567", "short_hash": "1234567", "author": "a", "date": "2026-01-01", "message": "x"}],
            "history_meta": {"returned_count": 1},
        }
        with patch("server.app._history_commits_impl", return_value=app.response_class(response=app.json.dumps(fake_history), status=200, mimetype="application/json")):
            v1 = self.client.get("/api/v1/history")
            legacy = self.client.get("/api/history")
        self.assertEqual(v1.status_code, 200)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(set(v1.get_json().keys()), set(legacy.get_json().keys()))

    def test_history_diff_contract_v1_and_legacy(self) -> None:
        fake_diff = {
            "from_commit": "1234567",
            "to_commit": "89abcde",
            "shortstat": "1 file changed",
            "changed_files": [{"status": "M", "path": "main.py"}],
            "status_counts": {"A": 0, "M": 1, "D": 0, "R": 0},
            "diff_excerpt": "diff --git",
            "truncated": False,
        }
        with patch("server.app._history_diff_impl", return_value=app.response_class(response=app.json.dumps(fake_diff), status=200, mimetype="application/json")):
            v1 = self.client.get("/api/v1/history-diff/1234567/89abcde")
            legacy = self.client.get("/api/history-diff/1234567/89abcde")
        self.assertEqual(v1.status_code, 200)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(set(v1.get_json().keys()), set(legacy.get_json().keys()))


if __name__ == "__main__":
    unittest.main()
