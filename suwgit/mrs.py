"""Merge requests, asked of the host's own command line: `glab` for GitLab, `gh` for GitHub.

Two questions and one change — which merge request is open for a branch, and
"merge into this branch instead" — so suwgit needs no token, no API client and no
login of its own: whoever is logged in to `glab` / `gh` is who acts.

Assumed: glab 1.x (`glab mr list --source-branch B -F json`, `glab mr update N --target-branch T`,
JSON fields `iid`, `web_url`, `target_branch`) and gh 2.x (`gh pr list --head B --json ...`,
`gh pr edit N --base T`).
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from . import gitops, host

REMOTE = "origin"


class MrError(Exception):
    """The host's command is missing, refused, or answered in a shape we do not know."""


@dataclass(frozen=True)
class Tool:
    cli: str  # the command
    prefix: str  # how its numbers are written: !12 or #12
    install: str


GITLAB = Tool("glab", "!", "https://gitlab.com/gitlab-org/cli#installation")
GITHUB = Tool("gh", "#", "https://cli.github.com")


@dataclass(frozen=True)
class MergeRequest:
    number: int
    url: str
    target: str

    def label(self, tool: Tool) -> str:
        return f"{tool.prefix}{self.number}"


def tool_for(root: Path) -> Tool:
    """The CLI that talks to the host of `origin`; only GitLab and GitHub hosts are known."""
    url = gitops.remote_url(root, REMOTE)
    if not url:
        raise MrError(f"{root} has no `{REMOTE}` remote — the merge requests are looked up on its host")
    name = urllib.parse.urlsplit(gitops.web_url(url)).hostname or ""
    if "github" in name:
        return GITHUB
    if "gitlab" in name:
        return GITLAB
    raise MrError(f"`{REMOTE}` is {url}: only GitLab (glab) and GitHub (gh) are supported for now")


def _run(root: Path, tool: Tool, *args: str) -> str:
    try:
        done = subprocess.run([tool.cli, *args], cwd=root, capture_output=True, check=False, **host.no_window())
    except OSError:
        raise MrError(
            f"`{tool.cli}` is not installed (or not on PATH) — install it from {tool.install} and log in with `{tool.cli} auth login`"
        ) from None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).decode("utf-8", errors="replace").strip()
        raise MrError(f"`{tool.cli} {' '.join(args[:2])}` failed: {detail or 'no output'}")
    return done.stdout.decode("utf-8", errors="replace")


def open_for(root: Path, tool: Tool, branch: str) -> MergeRequest | None:
    """The open merge request whose source is `branch` (the newest, should there be several), or None."""
    if tool is GITLAB:
        out = _run(root, tool, "mr", "list", "--source-branch", branch, "-F", "json")
        fields = ("iid", "web_url", "target_branch")
    else:
        out = _run(root, tool, "pr", "list", "--head", branch, "--state", "open", "--json", "number,url,baseRefName")
        fields = ("number", "url", "baseRefName")
    try:
        found = json.loads(out)
        if not found:
            return None
        number, url, target = (found[0][name] for name in fields)
        return MergeRequest(int(number), str(url), str(target))
    except (ValueError, KeyError, TypeError, IndexError):
        raise MrError(f"`{tool.cli}` answered in a shape suwgit does not know — its version may be too old or too new") from None


def retarget(root: Path, tool: Tool, mr: MergeRequest, target: str) -> None:
    if tool is GITLAB:
        _run(root, tool, "mr", "update", str(mr.number), "--target-branch", target)
    else:
        _run(root, tool, "pr", "edit", str(mr.number), "--base", target)
