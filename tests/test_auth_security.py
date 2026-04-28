from __future__ import annotations

import os
import unittest
from unittest.mock import patch

try:
    from server.app import app
except Exception as import_error:
    app = None
    APP_IMPORT_ERROR = import_error
else:
    APP_IMPORT_ERROR = None


class AuthSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if app is None:
            raise unittest.SkipTest(f"Flask app import unavailable in this environment: {APP_IMPORT_ERROR}")

    def setUp(self) -> None:
        self.client = app.test_client()

    def test_untrusted_identity_header_is_ignored_by_default(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CODEWEAVE_AUTH_MODE": "off",
                "CODEWEAVE_TRUST_CLIENT_IDENTITY": "off",
            },
            clear=False,
        ):
            response = self.client.get(
                "/api/v1/auth/security",
                environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
                headers={"X-Codeweave-User": "spoofed-user-id"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload.get("identity"), "ip:203.0.113.10")

    def test_identity_header_is_respected_when_explicitly_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CODEWEAVE_AUTH_MODE": "off",
                "CODEWEAVE_TRUST_CLIENT_IDENTITY": "on",
            },
            clear=False,
        ):
            response = self.client.get(
                "/api/v1/auth/security",
                environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
                headers={"X-Codeweave-User": "trusted-user-id"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload.get("identity"), "trusted-user-id")

    def test_session_or_csrf_protection_requires_configured_secret(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CODEWEAVE_AUTH_MODE": "off",
                "CODEWEAVE_SESSION_PROTECTION": "on",
                "CODEWEAVE_CSRF_PROTECTION": "off",
            },
            clear=False,
        ):
            os.environ.pop("CODEWEAVE_SECURITY_SECRET", None)
            response = self.client.get("/api/v1/auth/security", environ_overrides={"REMOTE_ADDR": "198.51.100.2"})
        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertEqual(payload.get("error_code"), "security_secret_required")


if __name__ == "__main__":
    unittest.main()
