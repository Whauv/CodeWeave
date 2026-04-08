from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from server.repository_service import _safe_extract_tar, normalize_github_repo_url


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


if __name__ == "__main__":
    unittest.main()
