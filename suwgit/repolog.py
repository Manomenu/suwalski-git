"""A short note left inside a repository saying why suwgit could not commit it.

The daemon is silent by design, and the central log at ~/.local/state mixes
every repository together. But the question "why has this project not been
committed for two days?" is asked while standing *in* that project, so the
answer belongs there too: `.gitsuw.log` in the repository root, newest last,
the last few blockers and nothing else.

Only real blockers are recorded — an unreachable model, a suspected secret, a
rejected push. Waiting because you are still editing is not a blocker, and
neither is having nothing to commit.

Writing the first note also puts the log into `.gitignore`, which is itself a
change — in a repository that had no `.gitignore`, that is suwgit making the
tree dirty. That is accepted: a blocker you never hear about is worse than one
extra ignored line, and it happens once per repository.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

LOG_NAME = ".gitsuw.log"
MAX_ENTRIES = 10
HEADER = (
    f"# {LOG_NAME} — why suwgit did not commit this repository. Newest last, "
    f"at most {MAX_ENTRIES} entries.\n"
    "# Written by the suwgit daemon; safe to delete, it will come back if the problem does.\n"
)
# "2026-09-11 08:25:12  LLM unavailable: …" with an optional "  (×3)" repeat count.
ENTRY_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s\s(.*?)(?:\s\s\(×(\d+)\))?$")


def _format(stamp: str, message: str, count: int) -> str:
    return f"{stamp}  {message}" + (f"  (×{count})" if count > 1 else "")


def _read_entries(path: Path) -> list[tuple[str, str, int]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = ENTRY_RE.match(line)
        if match:
            entries.append((match.group(1), match.group(2), int(match.group(3) or 1)))
        else:
            # Someone edited the file by hand; keep the line rather than eat it.
            entries.append(("", line, 1))
    return entries


def is_ignored(root: Path) -> bool:
    """Whether git already ignores the log — by any rule, from any ignore file.

    Asking git rather than reading `.gitignore` ourselves means a rule inherited
    from a global ignore file or `.git/info/exclude` counts too, and we do not
    add a line the repository does not need.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", LOG_NAME],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return done.returncode == 0


def ignore_entry(root: Path) -> bool:
    """Make sure .gitignore excludes the log. True if the file had to be changed.

    Without this the log itself is an uncommitted change, and the next sweep
    would ask the model to write a commit message about suwgit's own complaints.
    """
    if is_ignored(root):
        return False

    gitignore = root / ".gitignore"
    try:
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    if any(line.strip().lstrip("/") == LOG_NAME for line in lines):
        return False

    body = "\n".join(lines)
    if body and not body.endswith("\n"):
        body += "\n"
    gitignore.write_text(f"{body}{LOG_NAME}\n", encoding="utf-8")
    return True


def record_blocker(root: Path, message: str, now: float | None = None) -> None:
    """Append one blocker, keeping the file to MAX_ENTRIES.

    A blocker that keeps happening — a server that has been down all night —
    is counted rather than repeated, so ten hours of the same failure cannot
    push out the other nine things that went wrong.

    Never raises: a read-only checkout must not break the sweep over a note.
    """
    try:
        ignore_entry(root)

        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        message = " ".join(message.split())
        entries = _read_entries(root / LOG_NAME)

        if entries and entries[-1][1] == message:
            entries[-1] = (stamp, message, entries[-1][2] + 1)
        else:
            entries.append((stamp, message, 1))
        entries = entries[-MAX_ENTRIES:]

        target = root / LOG_NAME
        tmp = target.with_suffix(".log.tmp")
        tmp.write_text(HEADER + "".join(_format(*e) + "\n" for e in entries), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


def clear(root: Path) -> None:
    """Drop the log: the repository is unblocked.

    Called after suwgit commits or pushes, and also when a sweep finds nothing
    left to do — which is what a repository looks like after you have committed
    it by hand. Whoever cleared the blocker, the note is now a lie.

    The `.gitignore` line stays. It costs nothing, it is probably wanted anyway,
    and rewriting that file on every successful sweep is a worse idea than
    leaving one line behind.
    """
    try:
        (root / LOG_NAME).unlink(missing_ok=True)
    except OSError:
        pass
