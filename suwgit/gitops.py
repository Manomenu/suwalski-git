"""Everything suwgit does to a git repository.

Plain `git` subprocesses — no library — because the daemon must survive on a
machine where only git itself is installed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
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


def _git(root: Path | None, *args: str, env: dict[str, str] | None = None, with_stderr: bool = False) -> str:
    """git's stdout; with `with_stderr`, its stderr after it — where a push's `remote:` lines go."""
    cmd = ["git"]
    if root is not None:
        cmd += ["-C", str(root)]
    cmd += list(args)
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from None
    if done.returncode != 0:
        raise GitError((done.stderr or done.stdout).strip() or f"git {' '.join(args)} failed")
    return done.stdout + done.stderr if with_stderr else done.stdout


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


# Where a project names its commands — what a "how to test" may point to.
TASK_FILES = ("justfile", "Justfile", ".justfile", "Makefile", "Taskfile.yml", "package.json", "pyproject.toml")
MAX_TASK_FILE_CHARS = 2_000
MAX_TOP_LEVEL_ENTRIES = 80


def project_tooling(root: Path) -> str:
    """The top level of the repository and its task files, cut short: enough to know which commands exist."""
    try:
        names = sorted(entry.name + ("/" if entry.is_dir() else "") for entry in root.iterdir() if entry.name != ".git")
    except OSError:
        return ""
    parts = ["top level: " + "  ".join(names[:MAX_TOP_LEVEL_ENTRIES])]
    for name in TASK_FILES:
        try:
            text = (root / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name == "package.json":
            try:
                text = json.dumps({"scripts": json.loads(text).get("scripts", {})}, indent=2)
            except (ValueError, AttributeError):
                pass
        cut = "\n… (cut)" if len(text) > MAX_TASK_FILE_CHARS else ""
        parts.append(f"--- {name}\n{text[:MAX_TASK_FILE_CHARS]}{cut}")
    return "\n".join(parts)


def current_branch(root: Path) -> str | None:
    """The checked-out branch, or None on a detached HEAD (nothing to push to)."""
    try:
        return _git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip() or None
    except GitError:
        return None


# A tracker id the way YouTrack and Jira write them: letters, a dash, digits.
# Anchored on a letter boundary so `feature/EH-3311-csv` gives EH-3311, not H-3311.
_TICKET = re.compile(r"(?<![A-Za-z])[A-Za-z]+-\d+")


def ticket_in_branch(branch: str) -> str:
    """The first tracker id in a branch name, upper-cased — `feature/eh-3311-csv` is EH-3311 — or ""."""
    found = _TICKET.search(branch)
    return found.group(0).upper() if found else ""


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


@dataclass
class Pushed:
    summary: str  # what happened, one line
    merge_request_url: str = ""  # where to open (or see) a merge request for the branch, if anywhere


def push(root: Path) -> Pushed:
    """Push the current branch. Never forces, never rebases, never touches other branches.

    A branch with no upstream gets one (`-u`), because a commit the daemon made
    on a new local branch would otherwise sit there invisibly forever.
    """
    branch = current_branch(root)
    if branch is None:
        raise GitError("detached HEAD — no branch to push")

    if upstream_of(root, branch):
        # Asked before, not read from git's answer: that is translated on a Polish system.
        up_to_date = _git(root, "rev-parse", "HEAD").strip() == _git(root, "rev-parse", "@{upstream}").strip()
        try:
            output = _git(root, "push", with_stderr=True)
        except GitError as exc:
            raise GitError(_explain_push_failure(str(exc))) from None
        remote = _branch_remote(root, branch)
        summary = f"{branch} was already up to date on {remote or 'the remote'}" if up_to_date else f"pushed {branch}"
        return Pushed(summary, merge_request_url(root, remote, branch, output))

    names = remotes(root)
    if not names:
        raise GitError("no remote to push to")
    remote = "origin" if "origin" in names else names[0]
    try:
        output = _git(root, "push", "--set-upstream", remote, branch, with_stderr=True)
    except GitError as exc:
        raise GitError(_explain_push_failure(str(exc))) from None
    return Pushed(f"pushed {branch} and set its upstream to {remote}/{branch}", merge_request_url(root, remote, branch, output))


def _branch_remote(root: Path, branch: str) -> str:
    try:
        return _git(root, "config", f"branch.{branch}.remote").strip()
    except GitError:
        return ""


# GitLab, GitHub, Gitea and Bitbucket all answer a push of a branch with the
# link to open a merge request for it, on a `remote:` line of their own.
_REMOTE_URL = re.compile(r"^remote:\s+.*?(https?://\S+)", re.MULTILINE)


def merge_request_url(root: Path, remote: str, branch: str, push_output: str) -> str:
    """Where to open a merge request for `branch` after pushing it to `remote`, or "".

    The server's own link first: it knows its URL scheme, and on GitLab it
    links to the open merge request once there is one. GitHub only says it
    on the first push of a branch, so for GitHub and GitLab hosts the link is
    built from the remote's URL when the server stayed quiet. The branch the
    remote merges into has nothing to merge, so it gets no link.
    """
    found = _REMOTE_URL.search(push_output)
    if found:
        return found.group(1)
    if not remote or branch == default_branch(root, remote):
        return ""
    try:
        web = web_url(_git(root, "remote", "get-url", remote).strip())
    except GitError:
        return ""
    quoted = urllib.parse.quote(branch, safe="/")
    if web.startswith("https://github.com/"):
        return f"{web}/pull/new/{quoted}"
    if web and "gitlab" in (urllib.parse.urlsplit(web).hostname or ""):
        return f"{web}/-/merge_requests/new?merge_request%5Bsource_branch%5D={urllib.parse.quote(branch, safe='')}"
    return ""


def default_branch(root: Path, remote: str) -> str:
    """The branch `remote` merges into: its HEAD if this clone knows it, else main or master."""
    try:
        return _git(root, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD").strip().partition("/")[2]
    except GitError:
        pass
    for name in ("main", "master"):
        try:
            _git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{name}")
            return name
        except GitError:
            continue
    return ""


def web_url(remote_url: str) -> str:
    """https://host/group/repo for any of git's ways of writing a remote, or "" for a local one."""
    url = remote_url.strip()
    if re.match(r"^[\w.-]+@[\w.-]+:", url):  # scp-like: git@github.com:owner/repo.git
        url = "ssh://" + url.replace(":", "/", 1)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("ssh", "git", "http", "https") or not parts.hostname:
        return ""
    path = parts.path.strip("/").removesuffix(".git")
    return f"https://{parts.hostname}/{path}" if path else ""


def commit_all(root: Path, message: str) -> str:
    """Stage everything and commit. Returns the new short hash."""
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "--short", "HEAD").strip()


def _status_paths(status: str) -> list[str]:
    """Every path named by `git status --porcelain` output.

    Rename and copy entries carry two paths ("R  old -> new"); only the second
    one exists on disk, so that is the one we keep.
    """
    names = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        names.append(entry.strip().strip('"'))
    return names


def change_times(root: Path, status: str) -> tuple[float, float] | None:
    """(oldest, newest) modification time among the uncommitted files.

    Only files git already calls dirty are considered, which keeps build output
    and ignored junk out of it. None when nothing datable is left — every dirty
    path was deleted, or the tree is clean.
    """
    times = []
    for name in _status_paths(status):
        try:
            times.append((root / name).stat().st_mtime)
        except OSError:
            continue  # deleted, or a path we cannot stat — it says nothing about activity
    if not times:
        return None
    return min(times), max(times)


def seconds_since_last_change(root: Path, status: str, now: float | None = None) -> float | None:
    """How long ago the newest uncommitted file was touched, in seconds.

    This is the daemon's "are they still typing?" test, so it looks at
    modification times rather than at git.

    None when nothing datable is left. The caller reads that as "no reason to wait".
    """
    times = change_times(root, status)
    if times is None:
        return None
    return max(0.0, (time.time() if now is None else now) - times[1])


def global_user_name() -> str:
    """`git config --global user.name`, or empty if none is set."""
    try:
        return _git(None, "config", "--global", "user.name").strip()
    except GitError:
        return ""


def user_email(root: Path) -> str:
    """The committer email git would use in `root`, or empty if none is set."""
    try:
        return _git(root, "config", "user.email").strip()
    except GitError:
        return ""


def commit_messages(root: Path, since: str, author: str = "") -> list[tuple[str, str, str]]:
    """(full hash, author name, full message) of every commit on any branch since `since`, oldest first.

    `--all`, because work for a client may sit on a branch that is not merged
    yet, and still has to be reported.
    """
    if not has_head(root):
        return []
    args = ["log", "--all", "--reverse", f"--since={since}", "--format=%H%x1f%an%x1f%B%x1e"]
    if author:
        args.append(f"--author={author}")
    out = _git(root, *args)
    commits = []
    for record in out.split("\x1e"):
        if "\x1f" not in record:
            continue
        sha, name, message = record.split("\x1f", 2)
        commits.append((sha.strip(), name.strip(), message.strip()))
    return commits


REVERT_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
# How far back suwgit looks for the commit a set of changes exactly undoes.
REVERT_LOOKBACK = 20


def revert_marks(root: Path, since: str) -> list[tuple[str, date, str]]:
    """(revert hash, its day, the hash it names) for every revert since `since`, by anyone.

    What `git revert` writes — and what suwgit writes for an exact undo — is a
    line "This reverts commit <hash>.", which is all this reads.
    """
    if not has_head(root):
        return []
    out = _git(root, "log", "--all", f"--since={since}", "--grep=This reverts commit", "--format=%H%x1f%aI%x1f%B%x1e")
    marks = []
    for record in out.split("\x1e"):
        if record.count("\x1f") < 2:
            continue
        sha, when, message = record.split("\x1f", 2)
        day = datetime.fromisoformat(when.strip()).astimezone().date()
        for named in REVERT_RE.findall(message):
            marks.append((sha.strip(), day, named))
    return marks


def _patch(root: Path, old: str, new: str) -> str:
    return _git(root, "diff", "--full-index", "--no-color", "--no-renames", "--no-ext-diff", old, new)


def _working_tree_id(root: Path) -> str:
    """The tree the working tree would commit as, built in a scratch index — the real one is untouched."""
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        _git(root, "read-tree", "HEAD", env=env)
        _git(root, "add", "-A", env=env)
        return _git(root, "write-tree", env=env).strip()


def exact_revert_of(root: Path, lookback: int = REVERT_LOOKBACK) -> tuple[str, str] | None:
    """(hash, title) of a recent commit the uncommitted changes exactly undo, if there is one.

    "Exactly" is literal: the patch from HEAD to the working tree is the
    reverse of that commit's patch, byte for byte. That is what undoing a
    change by hand looks like — and what a model, which sees only the diff,
    would otherwise describe as new work. A partial undo is not caught here.
    """
    if not has_head(root):
        return None
    change = _patch(root, "HEAD", _working_tree_id(root))
    if not change.strip():
        return None
    log = _git(root, "log", "--no-merges", f"--max-count={lookback}", "--format=%H%x1f%P%x1f%s", "HEAD")
    for line in log.splitlines():
        sha, parents, title = line.split("\x1f", 2)
        if not parents.strip():
            continue  # the root commit has nothing to go back to
        if _patch(root, sha, parents.split()[0]) == change:
            return sha, title
    return None
