"""Git provenance helpers for ledger stages."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class LedgerGitError(RuntimeError):
    """Raised when git checks/provenance commands fail."""


@dataclass(frozen=True)
class GitWorkingTreeState:
    """State summary for a git working tree."""

    clean: bool
    detail: str = ""


def get_head_commit(repo_root: Path) -> str | None:
    """Return the current HEAD commit hash for a repository path.

    Returns ``None`` when the directory is not a git repository or git fails.
    """

    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def working_tree_state(repo_root: Path) -> GitWorkingTreeState:
    """Return whether a working tree is clean and a status summary if not."""

    porcelain_result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=False,
    )
    if porcelain_result.returncode != 0:
        raise LedgerGitError(f"unable to run git status in {repo_root}: {porcelain_result.stderr.strip()}")
    return GitWorkingTreeState(clean=porcelain_result.stdout.strip() == "", detail=porcelain_result.stdout)


def require_clean_working_tree(repo_root: Path) -> None:
    """Raise when the working tree for ``repo_root`` is dirty."""

    state = working_tree_state(repo_root)
    if state.clean:
        return

    raise LedgerGitError(
        "working tree has uncommitted changes\n"
        + (state.detail.strip() or "(git status --porcelain returned no detail)")
    )
