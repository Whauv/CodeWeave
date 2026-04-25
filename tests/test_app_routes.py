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

    def test_workspace_share_and_investigation_endpoints(self) -> None:
        create_workspace = self.client.post("/api/v1/workspaces", json={"name": "Wave5 Team"})
        self.assertEqual(create_workspace.status_code, 201)
        workspace = create_workspace.get_json()["workspace"]
        workspace_id = workspace["id"]

        workspaces = self.client.get("/api/v1/workspaces")
        self.assertEqual(workspaces.status_code, 200)
        self.assertGreaterEqual(len(workspaces.get_json().get("workspaces", [])), 1)

        share = self.client.post(
            "/api/v1/share-links",
            json={
                "workspace_id": workspace_id,
                "payload": {"scan_target": "C:/demo", "selected_node_id": "abc123"},
                "expires_hours": 24,
            },
        )
        self.assertEqual(share.status_code, 201)
        token = share.get_json()["share_link"]["token"]

        resolved = self.client.get(f"/api/v1/share-links/{token}")
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.get_json()["share_link"]["payload"]["selected_node_id"], "abc123")

        session_create = self.client.post(
            "/api/v1/investigations",
            json={"title": "Dependency risk drill", "workspace_id": workspace_id, "state": {"scan_target": "C:/demo"}},
        )
        self.assertEqual(session_create.status_code, 201)
        session_id = session_create.get_json()["session"]["id"]

        session_get = self.client.get(f"/api/v1/investigations/{session_id}")
        self.assertEqual(session_get.status_code, 200)
        session_patch = self.client.patch(
            f"/api/v1/investigations/{session_id}",
            json={"title": "Dependency risk drill (updated)", "state": {"scan_target": "C:/demo2"}},
        )
        self.assertEqual(session_patch.status_code, 200)
        session_list = self.client.get("/api/v1/investigations")
        self.assertEqual(session_list.status_code, 200)
        self.assertGreaterEqual(len(session_list.get_json().get("sessions", [])), 1)

    def test_pr_analyze_endpoint_contract(self) -> None:
        fake_graph = {
            "nodes": [
                {"id": "n1", "name": "handler", "file": "server/app.py", "churn_count": 8, "mutation_status": "hotspot"}
            ],
            "edges": [],
        }
        with patch(
            "server.app._load_latest_scan",
            return_value=(fake_graph, {"scan_root": str("."), "source_kind": "local", "language": "python"}),
        ), patch("server.app.is_git_repo", return_value=True), patch(
            "server.app._guess_changed_files_for_pr",
            return_value=([{"status": "M", "path": "server/app.py"}], {"source": "test", "base_commit": "a", "head_commit": "b"}),
        ):
            response = self.client.post(
                "/api/v1/pr/analyze",
                json={"pr_url": "https://github.com/acme/project/pull/42"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("impacted_nodes", payload)
        self.assertIn("hotspots", payload)
        self.assertIn("changed_files", payload)


if __name__ == "__main__":
    unittest.main()
