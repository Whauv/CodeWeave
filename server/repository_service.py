from __future__ import annotations

import io
import hashlib
import logging
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)
SNAPSHOT_TEMP_ROOT = Path(__file__).resolve().parents[1] / "history_snapshots_runtime"


def normalize_github_repo_url(url_value: str) -> str:
    parsed = urlparse(url_value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("GitHub URL must start with http:// or https://")
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Only github.com URLs are supported")

    path_parts = [segment for segment in parsed.path.split("/") if segment]
    if len(path_parts) < 2:
        raise ValueError("GitHub URL must include owner and repository name")

    owner = path_parts[0]
    repo = path_parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise ValueError("Invalid GitHub repository URL")
    return f"https://github.com/{owner}/{repo}.git"


def run_git_command(
    repo_root: Path,
    args: list[str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def clone_github_repo(repo_url: str, target_dir: Path, include_all_branches: bool = False) -> Path:
    try:
        clone_args = ["git", "clone", "--depth", "1"]
        if not include_all_branches:
            clone_args.append("--single-branch")
        clone_args.extend([repo_url, str(target_dir)])
        result = subprocess.run(clone_args, capture_output=True, text=True, check=False, timeout=300)
    except FileNotFoundError as exc:
        raise ValueError("Git is not installed or not available in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Timed out while cloning the GitHub repository") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        error_message = stderr or stdout or "Unknown git clone error"
        raise ValueError(f"Failed to clone repository: {error_message}")
    return target_dir


def fetch_all_remote_branches(repo_root: Path, depth: int = 160) -> subprocess.CompletedProcess[str]:
    return run_git_command(
        repo_root,
        [
            "fetch",
            "--prune",
            "--tags",
            "--depth",
            str(depth),
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
        ],
        timeout=300,
    )


def ensure_cached_repo(repo_url: str, include_all_branches: bool = False) -> Path:
    repo_hash = hashlib.md5(repo_url.encode("utf-8")).hexdigest()[:12]
    cache_root = Path(tempfile.gettempdir()) / "codeweave_repo_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    target_dir = cache_root / repo_hash

    if target_dir.exists() and (target_dir / ".git").exists():
        if include_all_branches:
            result = fetch_all_remote_branches(target_dir)
        else:
            result = subprocess.run(
                ["git", "-C", str(target_dir), "fetch", "--depth", "40", "origin"],
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
        if result.returncode != 0:
            LOGGER.warning(
                "Failed to refresh cached repo %s: %s",
                repo_url,
                result.stderr or result.stdout,
            )
        return target_dir

    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    return clone_github_repo(repo_url, target_dir, include_all_branches=include_all_branches)


def resolve_scan_source(project_input: str, include_all_branches: bool = False) -> tuple[Path, str, str]:
    if project_input.startswith(("http://", "https://")):
        repo_url = normalize_github_repo_url(project_input)
        clone_path = ensure_cached_repo(repo_url, include_all_branches=include_all_branches)
        return clone_path, repo_url, "github"
    resolved_path = Path(project_input).expanduser().resolve()
    if not resolved_path.exists() or not resolved_path.is_dir():
        raise ValueError("Invalid project path")
    return resolved_path, str(resolved_path), "local"


def is_git_repo(repo_root: Path) -> bool:
    return repo_root.exists() and (repo_root / ".git").exists()


def get_commit_count(repo_root: Path) -> int:
    result = run_git_command(repo_root, ["rev-list", "--count", "--all"], timeout=60)
    if result.returncode != 0:
        return 0
    try:
        return int((result.stdout or "0").strip())
    except ValueError:
        return 0


def get_head_branch(repo_root: Path) -> str:
    result = run_git_command(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=60)
    if result.returncode != 0:
        return "HEAD"
    return (result.stdout or "HEAD").strip()


def is_shallow_repository(repo_root: Path) -> bool:
    result = run_git_command(repo_root, ["rev-parse", "--is-shallow-repository"], timeout=60)
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip().lower() == "true"


def ensure_repo_history(repo_root: Path, desired_commits: int = 40) -> dict[str, Any]:
    before_count = get_commit_count(repo_root)
    shallow = is_shallow_repository(repo_root)
    fetched = False
    fetch_error = ""
    head_branch = get_head_branch(repo_root)

    if shallow and before_count < desired_commits:
        deepen_by = max(desired_commits * 2, 120)
        fetch_result = run_git_command(
            repo_root,
            ["fetch", "--unshallow", "--tags", "origin"],
            timeout=240,
        )
        fetched = fetch_result.returncode == 0
        if fetch_result.returncode != 0:
            fallback_result = run_git_command(
                repo_root,
                ["fetch", "--deepen", str(deepen_by), "--tags", "origin", head_branch],
                timeout=240,
            )
            fetched = fallback_result.returncode == 0
            if fallback_result.returncode != 0:
                fetch_error = (
                    fallback_result.stderr
                    or fallback_result.stdout
                    or fetch_result.stderr
                    or fetch_result.stdout
                    or ""
                ).strip()
                LOGGER.warning("Failed to deepen git history for %s: %s", repo_root, fetch_error)

    return {
        "before_count": before_count,
        "after_count": get_commit_count(repo_root),
        "is_shallow": is_shallow_repository(repo_root),
        "attempted_fetch": shallow and before_count < desired_commits,
        "fetched": fetched,
        "fetch_error": fetch_error,
        "head_branch": head_branch,
    }


def list_remote_branch_names(repo_root: Path) -> list[str]:
    result = run_git_command(
        repo_root,
        ["for-each-ref", "refs/remotes/origin", "--format=%(refname:short)"],
        timeout=60,
    )
    if result.returncode != 0:
        return []
    branches = [
        line.strip().replace("origin/", "", 1)
        for line in (result.stdout or "").splitlines()
        if line.strip() and line.strip() != "origin/HEAD"
    ]
    return sorted(set(branches))


def list_repo_commits(repo_root: Path, limit: int = 40) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not is_git_repo(repo_root):
        raise ValueError("Time-travel requires a git repository.")

    history_meta = ensure_repo_history(repo_root, desired_commits=limit)
    result = run_git_command(
        repo_root,
        [
            "log",
            f"--max-count={limit}",
            "--all",
            "--date-order",
            "--reverse",
            "--date=short",
            "--pretty=format:%H%x1f%h%x1f%ad%x1f%an%x1f%s",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "Failed to read git history")

    commits: list[dict[str, str]] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        full_hash, short_hash, date_value, author, subject = parts
        commits.append(
            {
                "hash": full_hash,
                "short_hash": short_hash,
                "date": date_value,
                "author": author,
                "message": subject,
            }
        )
    history_meta["returned_count"] = len(commits)
    history_meta["branch_names"] = list_remote_branch_names(repo_root)
    return commits, history_meta


def diff_commits(
    repo_root: Path,
    from_commit: str,
    to_commit: str,
    max_files: int = 80,
) -> dict[str, Any]:
    if not is_git_repo(repo_root):
        raise ValueError("Diff requires a git repository.")

    stat_result = run_git_command(
        repo_root,
        ["diff", "--shortstat", from_commit, to_commit],
        timeout=120,
    )
    if stat_result.returncode != 0:
        raise ValueError(stat_result.stderr.strip() or "Failed to compute commit diff summary")

    files_result = run_git_command(
        repo_root,
        ["diff", "--name-status", from_commit, to_commit],
        timeout=120,
    )
    if files_result.returncode != 0:
        raise ValueError(files_result.stderr.strip() or "Failed to compute commit diff files")

    changed_files: list[dict[str, str]] = []
    for line in (files_result.stdout or "").splitlines():
        parts = line.strip().split("\t")
        if not parts:
            continue
        status = parts[0].strip() or "M"
        if status.startswith("R") and len(parts) >= 3:
            changed_files.append({"status": "R", "old_path": parts[1], "path": parts[2]})
        elif len(parts) >= 2:
            changed_files.append({"status": status[0], "path": parts[1]})
        if len(changed_files) >= max_files:
            break

    return {
        "from_commit": from_commit,
        "to_commit": to_commit,
        "shortstat": (stat_result.stdout or "").strip() or "No file-level changes detected.",
        "changed_files": changed_files,
        "truncated": len(changed_files) >= max_files,
    }


def _safe_extract_tar(tar_file: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in tar_file.getmembers():
        member_path = (destination / member.name).resolve()
        if destination not in member_path.parents and member_path != destination:
            raise ValueError("Unsafe archive member detected while extracting commit snapshot")
    try:
        tar_file.extractall(destination, filter="data")
    except TypeError:
        tar_file.extractall(destination)


def extract_commit_snapshot(repo_root: Path, commit_hash: str) -> Path:
    SNAPSHOT_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = SNAPSHOT_TEMP_ROOT / f"codeweave_history_{commit_hash[:12]}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(repo_root), "archive", "--format=tar", commit_hash],
        capture_output=True,
        check=False,
        timeout=240,
    )
    if archive.returncode != 0:
        raise ValueError("Failed to export commit snapshot")

    extract_root = temp_dir / "repo"
    extract_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar_file:
        _safe_extract_tar(tar_file, extract_root)
    return extract_root
