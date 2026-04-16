from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess

from server.repository_service import _safe_extract_tar, diff_commits, normalize_github_repo_url


class RepositoryServiceTests(unittest.TestCase):
    def test_normalize_github_repo_url_handles_web_url(self) -> None:
        normalized = normalize_github_repo_url("https://github.com/openai/codemapper")
        self.assertEqual(normalized, "https://github.com/openai/codemapper.git")

    def test_safe_extract_tar_rejects_path_traversal(self) -> None:
        file_buffer = io.BytesIO()
        with tarfile.open(fileobj=file_buffer, mode="w") as tar:
            info = tarfile.TarInfo("../escape.txt")
            payload = b"unsafe"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))

        file_buffer.seek(0)
        temp_dir = Path(tempfile.mkdtemp(dir=str(Path.cwd())))
        try:
            with tarfile.open(fileobj=file_buffer, mode="r") as tar:
                with self.assertRaises(ValueError):
                    _safe_extract_tar(tar, temp_dir)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_diff_commits_returns_expected_contract(self) -> None:
        def fake_run_git_command(_repo_root: Path, args: list[str], timeout: int = 120) -> CompletedProcess[str]:
            if args[:2] == ["diff", "--shortstat"]:
                return CompletedProcess(args, 0, stdout=" 1 file changed, 2 insertions(+)\n", stderr="")
            if args[:2] == ["diff", "--name-status"]:
                return CompletedProcess(args, 0, stdout="M\tapp.py\nA\ttests/test_app.py\n", stderr="")
            if args[:2] == ["diff", "--no-color"]:
                return CompletedProcess(args, 0, stdout="diff --git a/app.py b/app.py\n@@ -1 +1 @@\n", stderr="")
            return CompletedProcess(args, 1, stdout="", stderr="unsupported")

        with patch("server.repository_service.is_git_repo", return_value=True), patch(
            "server.repository_service.run_git_command", side_effect=fake_run_git_command
        ):
            payload = diff_commits(Path.cwd(), "abc", "def")

        self.assertIn("shortstat", payload)
        self.assertIn("changed_files", payload)
        self.assertIn("truncated", payload)
        self.assertIn("status_counts", payload)
        self.assertIn("diff_excerpt", payload)
        self.assertEqual(payload["status_counts"]["M"], 1)
        self.assertEqual(payload["status_counts"]["A"], 1)


if __name__ == "__main__":
    unittest.main()
