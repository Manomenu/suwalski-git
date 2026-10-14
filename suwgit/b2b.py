"""The B2B commit format: writing it, and reading it back for a report.

A B2B commit is an ordinary suwgit commit with a record of the work under the
title. Everything after the title is fixed-shape, so `suwgit report` can read
the history back without a database — the git log is the database:

    [feature,bugfix] added csv export for revenue chart and fixed main tab not opening

    Summary: The revenue chart can now be downloaded as a spreadsheet, and the main tab opens again after a long break.

    Work-Scope:
    - added a button that downloads the revenue chart data as a spreadsheet
    - fixed the main tab not opening after the user had been away for a while

    Project: dashboard
    Tenant: lokum
    Work-Period: 2026-10-12T09:14+02:00/2026-10-12T11:04+02:00
    Work-Id: 3f9c1a7be210
    Language: en

`Project`, `Tenant`, `Work-Period`, `Work-Id` and `Language` are git trailers (`git log
--format=%(trailers)` reads them too). The period is an ISO 8601 interval with
the UTC offset in it, so it means the same thing on any machine and sorts as text.

The title stays technical, for you and `git log`. `Summary` and `Work-Scope`
are written for a non-technical client, and they are what the report shows.

`Tenant` is the key a report is filtered by — your working name for the client.
It is never printed in the report; `Project` is what the client reads.

Both halves of the format live in this one module, so they cannot drift apart.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import gitops

SUMMARY = "Summary"
WORK_SCOPE = "Work-Scope"
PROJECT = "Project"
TENANT = "Tenant"
WORK_PERIOD = "Work-Period"
WORK_ID = "Work-Id"
LANGUAGE = "Language"

# The last touch of a file is not the end of the work on it: it was saved, and
# then looked at. Two minutes is a modest allowance, never stretched past now.
END_ALLOWANCE_SECONDS = 120

SUMMARY_RE = re.compile(rf"^{SUMMARY}:[ \t]*(.+)$", re.MULTILINE)
TRAILER_RE = re.compile(rf"^({PROJECT}|{TENANT}|{WORK_PERIOD}|{WORK_ID}|{LANGUAGE}):[ \t]*(.*)$", re.MULTILINE)
# What `git cherry-pick -x` adds to a copy: the same work, so it must not tell
# the copy apart from the original.
CHERRY_PICK_RE = re.compile(r"^\(cherry picked from commit [0-9a-f]+\)$", re.MULTILINE)


@dataclass
class Entry:
    """One B2B commit as a report needs it."""

    repo: Path
    commit: str
    author: str
    title: str
    summary: str  # empty in a commit made before summaries existed
    project: str
    tenant: str
    start: datetime
    end: datetime
    work_scope: list[str]
    work_id: str = ""  # empty in a commit made before Work-Ids existed
    reverted: date | None = None  # the day a revert undid it, if one did
    message: str = ""  # as committed, without a cherry-pick note; for `key`
    language: str = "en"  # what its summary and work scope are written in; English before the trailer existed

    @property
    def key(self) -> str:
        """What makes two commits the same piece of work.

        The Work-Id, which cherry-pick, rebase and amend carry over unchanged;
        for an older commit without one, its message, which they carry too.
        """
        return self.work_id or "message:" + hashlib.sha1(self.message.encode("utf-8")).hexdigest()


def _local(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp).astimezone().replace(second=0, microsecond=0)


def work_period(times: tuple[float, float] | None, now: float) -> tuple[datetime, datetime]:
    """From the first uncommitted change to the last one plus an allowance, capped at now.

    `times` is (oldest, newest) modification time of the changed files; None
    when there is nothing to date (only deletions), which makes it a moment.
    """
    if times is None:
        return _local(now), _local(now)
    oldest, newest = times
    end = min(newest + END_ALLOWANCE_SECONDS, now)
    return _local(min(oldest, end)), _local(end)


def format_period(start: datetime, end: datetime) -> str:
    return f"{start.isoformat(timespec='minutes')}/{end.isoformat(timespec='minutes')}"


def new_work_id() -> str:
    return secrets.token_hex(6)


def compose(
    title: str,
    summary: str,
    work_scope: list[str],
    project: str,
    tenant: str,
    start: datetime,
    end: datetime,
    work_id: str = "",
    notes: str = "",
    language: str = "",
) -> str:
    """The full B2B commit message. A `work_id` (see `new_work_id`) is added as a trailer.

    `notes` — the how-to-test and how-to-review checklists of `-i` — go between
    the work scope and the trailers: the trailers must stay the last paragraph
    for git to read them as trailers, and the report reads the scope up to its
    first line that is not an item.
    """
    scope = "\n".join(f"- {item}" for item in work_scope)
    trailers = f"{PROJECT}: {project}\n{TENANT}: {tenant}\n{WORK_PERIOD}: {format_period(start, end)}"
    if work_id:
        trailers += f"\n{WORK_ID}: {work_id}"
    if language:
        trailers += f"\n{LANGUAGE}: {language}"
    notes = f"{notes}\n\n" if notes else ""
    return f"{title}\n\n{SUMMARY}: {summary}\n\n{WORK_SCOPE}:\n{scope}\n\n{notes}{trailers}"


def parse_period(text: str) -> tuple[datetime, datetime] | None:
    try:
        start, end = (datetime.fromisoformat(part.strip()) for part in text.split("/"))
    except ValueError:
        return None
    if start.tzinfo is None or end.tzinfo is None:
        return None
    return start, end


def parse(message: str, repo: Path, commit: str, author: str = "") -> Entry | None:
    """Read a commit message back. None if it is not a B2B commit."""
    lines = message.strip().splitlines()
    if not lines:
        return None

    trailers = {key: value.strip() for key, value in TRAILER_RE.findall(message)}
    period = parse_period(trailers.get(WORK_PERIOD, ""))
    if period is None:
        return None

    scope = []
    if f"{WORK_SCOPE}:" in lines:
        for line in lines[lines.index(f"{WORK_SCOPE}:") + 1 :]:
            if not line.startswith("- "):
                break
            scope.append(line[2:].strip())
    if not scope:
        return None

    return Entry(
        repo=repo,
        commit=commit,
        author=author,
        title=lines[0].strip(),
        summary=summary.group(1).strip() if (summary := SUMMARY_RE.search(message)) else "",
        project=trailers.get(PROJECT) or repo.name,
        tenant=trailers.get(TENANT, ""),
        start=period[0],
        end=period[1],
        work_scope=scope,
        work_id=trailers.get(WORK_ID, ""),
        language=trailers.get(LANGUAGE) or "en",
        message=CHERRY_PICK_RE.sub("", message).strip(),
    )


def _undone(reverts: dict[str, tuple[date, str]], known: list[str]):
    """Which commits a revert undid, and when, as a function of a full hash.

    `reverts` maps a revert commit to (its day, the hash it names — as written,
    possibly abbreviated). A revert that was itself reverted undoes nothing:
    the work is back.
    """
    targets: dict[str, list[str]] = {}
    for revert, (_, named) in reverts.items():
        full = next((sha for sha in known if sha.startswith(named)), named)
        targets.setdefault(full, []).append(revert)

    memo: dict[str, date | None] = {}

    def undone(sha: str, depth: int = 0) -> date | None:
        if sha in memo:
            return memo[sha]
        days = [reverts[r][0] for r in targets.get(sha, []) if depth < 50 and undone(r, depth + 1) is None]
        memo[sha] = min(days) if days else None
        return memo[sha]

    return undone


def collect(repos: list[Path], tenant: str, first: date, last: date) -> list[Entry]:
    """Every B2B piece of work of yours for `tenant` started between `first` and `last`, inclusive.

    Read straight from each repository's history. Only commits by the email git
    uses in that repository count — in a shared repository, a colleague's B2B
    commits are their record, not yours.

    One piece of work can sit in several commits: cherry-picked to another
    branch, rebased, amended, kept on a backup branch, or seen from two
    worktrees. They count once (see `Entry.key`). A piece of work a revert
    undid — any copy of it, by anyone — is marked `reverted`, not left out.
    """
    # A commit is made after its work began, so nothing older than the first
    # day can belong in the report. A day of slack covers commit dates written
    # in another timezone; git reads a bare date as that date at the current
    # time of day, hence the explicit midnight.
    since = f"{(first - timedelta(days=1)).isoformat()} 00:00"

    copies: dict[str, list[Entry]] = {}
    reverts: dict[str, tuple[date, str]] = {}
    seen: set[str] = set()
    for root in repos:
        if not (root / ".git").exists():
            continue
        try:
            commits = gitops.commit_messages(root, since=since, author=gitops.user_email(root))
            marks = gitops.revert_marks(root, since=since)
        except gitops.GitError:
            continue
        for revert, day, named in marks:
            reverts.setdefault(revert, (day, named))
        for sha, author, message in commits:
            if sha in seen:
                continue
            seen.add(sha)
            entry = parse(message, root, sha, author)
            if entry and entry.tenant == tenant:
                copies.setdefault(entry.key, []).append(entry)

    undone = _undone(reverts, list(seen | set(reverts)))
    entries = []
    for group in copies.values():
        entry = min(group, key=lambda e: e.start)
        days = [day for copy in group if (day := undone(copy.commit))]
        entry.reverted = min(days) if days else None
        if first <= entry.start.astimezone().date() <= last:
            entries.append(entry)
    entries.sort(key=lambda e: (e.project.lower(), e.start))
    return entries
