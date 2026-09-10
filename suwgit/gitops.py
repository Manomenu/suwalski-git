"""Everything suwgit does to a git repository.

Plain `git` subprocesses — no library — because the daemon must survive on a
machine where only git itself is installed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Fallback budget when no config is passed. The real one lives in the config,
# because it depends entirely on the backend's context window — measured against
# Qwen3-8B, code tokenises at 3.78 chars/token, so a 200,100-token
# window holds roughly 740,000 characters of diff before anything else.
DEFAULT_MAX_DIFF_CHARS = 400_000
MAX_UNTRACKED_FILES = 40
MAX_UNTRACKED_LINES = 30

# A repository in the middle of one of these is not ours to commit.
IN_PROGRESS_MARKERS = ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG")


class GitError(Exception):
    """A git command failed, or the path is not inside a repository."""


@dataclass
class WorkingTree:
    """The uncommitted state of one repository, ready to hand to the model."""

    root: Path
    status: str
    stat: str
    diff: str

    @property
    def is_dirty(self) -> bool:
        return bool(self.status.strip())


def _git(root: Path | None, *args: str) -> str:
    cmd = ["git"]
    if root is not None:
        cmd += ["-C", str(root)]
    cmd += list(args)
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from None
    if done.returncode != 0:
        raise GitError((done.stderr or done.stdout).strip() or f"git {' '.join(args)} failed")
    return done.stdout


def find_repo_root(path: Path) -> Path:
    """Closest repository root at or above `path` — what `suwgit commit` walks up to find."""
    target = path.expanduser().resolve()
    if not target.exists():
        raise GitError(f"no such path: {target}")
    start = target if target.is_dir() else target.parent
    try:
        out = _git(start, "rev-parse", "--show-toplevel")
    except GitError:
        raise GitError(
            f"no git repository found: walked up from {start} to {start.anchor} without finding a .git — "
            f"run this inside a repository, or `git init` first"
        ) from None
    return Path(out.strip()).resolve()


def remotes(root: Path) -> list[str]:
    return [line for line in _git(root, "remote").splitlines() if line.strip()]


def has_head(root: Path) -> bool:
    """False in a repository that has no commit yet."""
    try:
        _git(root, "rev-parse", "--verify", "HEAD")
    except GitError:
        return False
    return True


def in_progress_operation(root: Path) -> str | None:
    """Name of an interrupted merge/rebase/cherry-pick, if one is underway."""
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir").strip())
    for marker in IN_PROGRESS_MARKERS:
        if (git_dir / marker).exists():
            return marker
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return "REBASE"
    return None


def _untracked_preview(root: Path, status: str) -> str:
    """New files never show up in `git diff`, so show their heads by hand."""
    names = [line[3:].strip('"') for line in status.splitlines() if line.startswith("??")]
    chunks = []
    for name in names[:MAX_UNTRACKED_FILES]:
        path = root / name
        if path.is_dir() or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head = "\n".join(text.splitlines()[:MAX_UNTRACKED_LINES])
        chunks.append(f"--- new file: {name}\n{head}")
    return "\n".join(chunks)


def read_working_tree(root: Path, max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS) -> WorkingTree:
    """Collect status, diffstat and diff for the whole working tree.

    Truncation is a last resort, and a cheap one: `status` is always included in
    full, so even a clipped diff leaves the model every filename that changed.
    (`--stat` covers tracked changes only — an untracked file has no stat line.)
    """
    # -uall, not the default: git otherwise collapses a new directory to a single
    # "?? deploy/" line, and every file inside it stays invisible to the model.
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    base = ["diff", "HEAD"] if has_head(root) else ["diff"]
    stat = _git(root, *base, "--stat")
    diff = _git(root, *base)

    untracked = _untracked_preview(root, status)
    if untracked:
        diff = f"{diff}\n{untracked}" if diff.strip() else untracked

    if len(diff) > max_diff_chars:
        diff = diff[:max_diff_chars] + f"\n… (diff truncated at {max_diff_chars} characters)"

    return WorkingTree(root=root, status=status, stat=stat, diff=diff)


def current_branch(root: Path) -> str | None:
    """The checked-out branch, or None on a detached HEAD (nothing to push to)."""
    try:
        return _git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip() or None
    except GitError:
        return None


def upstream_of(root: Path, branch: str) -> str | None:
    try:
        return _git(root, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}").strip() or None
    except GitError:
        return None


def pending_commits(root: Path) -> int:
    """How many commits on HEAD have reached no remote yet.

    `HEAD --not --remotes` rather than `@{upstream}..HEAD`, so it is right for a
    branch with no upstream too (everything is pending), and does not re-push a
    commit that already landed on some other remote branch.
    """
    if not has_head(root):
        return 0
    try:
        return int(_git(root, "rev-list", "--count", "HEAD", "--not", "--remotes").strip())
    except (GitError, ValueError):
        return 0


def _explain_push_failure(detail: str) -> str:
    """Turn git's multi-line rejection into one line that says what to do.

    git is talkative when it refuses a push — seven lines of `hint:` about
    running `git pull`. That is good advice for a human at a terminal and noise
    in a daemon log, where the only question is why the commit is still local.
    """
    lines = [line.strip() for line in detail.splitlines() if line.strip() and not line.startswith("hint:")]
    text = " ".join(lines)
    low = text.lower()

    if "[rejected]" in low or "non-fast-forward" in low or "fetch first" in low:
        return (
            "rejected as non-fast-forward — the remote has commits this clone does not. "
            "suwgit never force-pushes and never pulls on your behalf: resolve it yourself "
            "(e.g. git pull --rebase), and the commit goes out on the next sweep"
        )
    if "permission denied" in low or "publickey" in low or "authentication failed" in low:
        return f"authentication failed (is the SSH agent reachable from the daemon?): {text}"
    if "could not read from remote" in low or "does not appear to be a git repository" in low:
        return f"remote unreachable: {text}"
    return text


def push(root: Path) -> str:
    """Push the current branch. Never forces, never rebases, never touches other branches.

    A branch with no upstream gets one (`-u`), because a commit the daemon made
    on a new local branch would otherwise sit there invisibly forever.
    """
    branch = current_branch(root)
    if branch is None:
        raise GitError("detached HEAD — no branch to push")

    if upstream_of(root, branch):
        try:
            _git(root, "push")
        except GitError as exc:
            raise GitError(_explain_push_failure(str(exc))) from None
        return f"pushed {branch}"

    names = remotes(root)
    if not names:
        raise GitError("no remote to push to")
    remote = "origin" if "origin" in names else names[0]
    try:
        _git(root, "push", "--set-upstream", remote, branch)
    except GitError as exc:
        raise GitError(_explain_push_failure(str(exc))) from None
    return f"pushed {branch} and set its upstream to {remote}/{branch}"


def commit_all(root: Path, message: str) -> str:
    """Stage everything and commit. Returns the new short hash."""
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "--short", "HEAD").strip()
