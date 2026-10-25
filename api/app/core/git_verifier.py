"""
Git origin content integrity verification module (Offline / Testing Tooling).

NOTE: This is an offline tooling utility for environments with direct local access
to a Git repository database. It is NOT invoked during live HTTP document uploads
because the AI-Hub server does not host a local Git clone of user repositories.
In HTTP upload contexts, Git commit hashes are recorded as client assertions
(provenance_assertion: 'client_git_commit') rather than server-verified blobs.
"""

import hashlib
import re
import subprocess
from pathlib import Path


def get_git_commit_blob_bytes(
    repo_path: str | Path,
    commit_sha: str,
    relative_path: str,
) -> tuple[bytes | None, str | None]:
    """Retrieve raw file bytes from git object database at commit_sha:relative_path.

    Returns:
        (blob_bytes, error_message)
    """
    if not re.match(r"^[0-9a-fA-F]{40}$", commit_sha):
        return None, f"Invalid commit SHA format: {commit_sha}"

    repo = Path(repo_path)
    if not (repo / ".git").exists() and not (repo / "HEAD").exists():
        return None, f"Repository path '{repo_path}' is not a valid Git repository"

    # Normalize POSIX path
    posix_path = relative_path.replace("\\", "/").lstrip("/")

    cmd = ["git", "cat-file", "blob", f"{commit_sha}:{posix_path}"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            return None, f"Git blob lookup failed for {commit_sha[:8]}:{posix_path}: {err}"
        return proc.stdout, None
    except Exception as e:
        return None, f"Failed to execute git command: {str(e)}"


def verify_git_content_integrity(
    repo_path: str | Path,
    commit_sha: str,
    relative_path: str,
    file_bytes: bytes,
) -> tuple[bool, str | None, str | None]:
    """Verify that file_bytes cryptographically matches the git commit blob.

    Returns:
        (is_valid, git_sha256, error_message)
    """
    blob_bytes, err = get_git_commit_blob_bytes(repo_path, commit_sha, relative_path)
    if err or blob_bytes is None:
        return False, None, err

    git_sha256 = hashlib.sha256(blob_bytes).hexdigest()
    uploaded_sha256 = hashlib.sha256(file_bytes).hexdigest()

    if git_sha256 != uploaded_sha256:
        return (
            False,
            git_sha256,
            (
                f"Content integrity mismatch: uploaded SHA-256 '{uploaded_sha256}' "
                f"does not match Git commit {commit_sha[:8]} blob SHA-256 '{git_sha256}'"
            ),
        )

    return True, git_sha256, None
