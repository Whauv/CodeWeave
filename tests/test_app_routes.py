from __future__ import annotations

import uuid
import unittest
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


class AppRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if app is None or STATE is None:
            raise unittest.SkipTest(f"Flask app import unavailable in this environment: {APP_IMPORT_ERROR}")

    def setUp(self) -> None:
        self.client = app.test_client()
        self.client.environ_base["HTTP_X_CODEWEAVE_USER"] = f"routes-{uuid.uuid4().hex}"
        STATE.reset()

    def test_languages_endpoint_returns_supported_options(self) -> None:
        response = self.client.get("/api/languages")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("languages", payload)
        self.assertGreaterEqual(len(payload["languages"]), 4)

    def test_metrics_endpoint_exposes_prometheus_text(self) -> None:
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("codeweave_http_requests_total", text)
        self.assertIn("codeweave_jobs_total", text)

    def test_health_ready_returns_schema_version(self) -> None:
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("schema_version", payload)

    def test_graph_endpoint_requires_prior_scan(self) -> None:
        response = self.client.get("/api/graph")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "No graph scanned yet")

    def test_insights_endpoint_requires_prior_scan(self) -> None:
        response = self.client.get("/api/insights")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "No graph scanned yet")

    def test_history_endpoint_requires_prior_scan(self) -> None:
        response = self.client.get("/api/history")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "No graph scanned yet")

    def test_scan_rejects_invalid_path(self) -> None:
        response = self.client.post(
            "/api/scan",
            json={"path": "C:/this/path/does/not/exist", "language": "python"},
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("error", payload)
        self.assertEqual(payload.get("error_code"), "scan_source_validation_failed")

    def test_chat_requires_graph_context(self) -> None:
        response = self.client.post("/api/chat", json={"message": "What breaks?"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "No graph scanned yet")

    def test_history_diff_endpoint_contract_canonical_and_legacy(self) -> None:
        diff_payload = {
            "from_commit": "abc1234",
            "to_commit": "def4567",
            "shortstat": "1 file changed, 3 insertions(+)",
            "changed_files": [{"status": "M", "path": "app.py"}],
            "status_counts": {"A": 0, "M": 1, "D": 0, "R": 0},
            "diff_excerpt": "diff --git a/app.py b/app.py",
            "truncated": False,
        }
        with patch("server.app._load_latest_scan", return_value=({}, {"scan_root": str("."), "source_kind": "local", "language": "python"})), patch(
            "server.app.is_git_repo", return_value=True
        ), patch("server.app.diff_commits", return_value=diff_payload):
            canonical = self.client.get("/api/history-diff/abc1234/def4567")
            legacy = self.client.get("/api/history/diff/abc1234/def4567")

        self.assertEqual(canonical.status_code, 200)
        self.assertEqual(legacy.status_code, 200)
        canonical_payload = canonical.get_json()
        legacy_payload = legacy.get_json()
        for payload in (canonical_payload, legacy_payload):
            self.assertIn("shortstat", payload)
            self.assertIn("changed_files", payload)
            self.assertIn("truncated", payload)
            self.assertIn("status_counts", payload)

    def test_history_snapshot_rejects_invalid_commit_hash(self) -> None:
        with patch("server.app._load_latest_scan", return_value=({}, {"scan_root": str("."), "source_kind": "local", "language": "python"})):
            response = self.client.get("/api/history/not-a-hash")
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload.get("error_code"), "invalid_commit_hash")


if __name__ == "__main__":
    unittest.main()
