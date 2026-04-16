from __future__ import annotations

import unittest

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
        STATE.graph_cache = None
        STATE.scan_context = None
        STATE.history_graph_cache.clear()

    def test_languages_endpoint_returns_supported_options(self) -> None:
        response = self.client.get("/api/languages")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("languages", payload)
        self.assertGreaterEqual(len(payload["languages"]), 4)

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
        self.assertIn("error", response.get_json())

    def test_chat_requires_graph_context(self) -> None:
        response = self.client.post("/api/chat", json={"message": "What breaks?"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "No graph scanned yet")


if __name__ == "__main__":
    unittest.main()
