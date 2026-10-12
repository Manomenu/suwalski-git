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

`Project`, `Tenant` and `Work-Period` are git trailers (`git log
--format=%(trailers)` reads them too). The period is an ISO 8601 interval with
the UTC offset in it, so it means the same thing on any machine and sorts as text.

The title stays technical, for you and `git log`. `Summary` and `Work-Scope`
are written for a non-technical client, and they are what the report shows.

`Tenant` is the key a report is filtered by — your working name for the client.
It is never printed in the report; `Project` is what the client reads.

Both halves of the format live in this one module, so they cannot drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import gitops

SUMMARY = "Summary"
WORK_SCOPE = "Work-Scope"
PROJECT = "Project"
TENANT = "Tenant"
WORK_PERIOD = "Work-Period"

# The last touch of a file is not the end of the work on it: it was saved, and
# then looked at. Two minutes is a modest allowance, never stretched past now.
END_ALLOWANCE_SECONDS = 120

SUMMARY_RE = re.compile(rf"^{SUMMARY}:[ \t]*(.+)$", re.MULTILINE)
TRAILER_RE = re.compile(rf"^({PROJECT}|{TENANT}|{WORK_PERIOD}):[ \t]*(.*)$", re.MULTILINE)


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


def compose(title: str, summary: str, work_scope: list[str], project: str, tenant: str, start: datetime, end: datetime) -> str:
    """The full B2B commit message."""
    scope = "\n".join(f"- {item}" for item in work_scope)
    trailers = f"{PROJECT}: {project}\n{TENANT}: {tenant}\n{WORK_PERIOD}: {format_period(start, end)}"
    return f"{title}\n\n{SUMMARY}: {summary}\n\n{WORK_SCOPE}:\n{scope}\n\n{trailers}"


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
    )


def collect(repos: list[Path], tenant: str, first: date, last: date) -> list[Entry]:
    """Every B2B commit of yours for `tenant` whose work started between `first` and `last`, inclusive.

    Read straight from each repository's history. Only commits by the email git
    uses in that repository count — in a shared repository, a colleague's B2B
    commits are their record, not yours.
    """
    entries = []
    for root in repos:
        if not (root / ".git").exists():
            continue
        try:
            # A commit is made after its work began, so nothing older than the
            # first day can belong in the report. A day of slack covers commit
            # dates written in another timezone; git reads a bare date as that
            # date at the current time of day, hence the explicit midnight.
            since = f"{(first - timedelta(days=1)).isoformat()} 00:00"
            commits = gitops.commit_messages(root, since=since, author=gitops.user_email(root))
        except gitops.GitError:
            continue
        for sha, author, message in commits:
            entry = parse(message, root, sha, author)
            if entry and entry.tenant == tenant and first <= entry.start.astimezone().date() <= last:
                entries.append(entry)
    entries.sort(key=lambda e: (e.project.lower(), e.start))
    return entries
