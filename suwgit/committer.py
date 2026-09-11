"""The one operation suwgit performs: look at a dirty repository, ask the
model what happened there, commit under that name.

Shared by `suwgit commit` (foreground, prints) and the daemon (background,
logs only), so both make exactly the same decisions about what is committable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import gitops, repolog
from .config import Config
from .llm import LlmUnavailable, suggest_commit_message
from .locking import Busy, repo_lock
from .logs import logger


@dataclass
class Result:
    root: Path
    committed: bool
    reason: str
    message: str = ""
    commit: str = ""
    pushed: str = ""  # what the push did, empty if it did not happen
    push_error: str = ""  # why it did not, if it was meant to
    unsafe: bool = False  # the model saw something that must not reach a git history
    deferred: bool = False  # left alone because the files are still being edited


def commit_repo(config: Config, root: Path, push: bool | None = None, quiet_seconds: float = 0.0) -> Result:
    """Commit everything in `root` under an LLM-written message, then push it.

    A clean tree is not the end of the story: commits you made by hand, or ones
    whose push failed on an earlier sweep, are still waiting to go out. So when
    pushing is on, a repository with nothing to commit but something unpushed is
    pushed anyway.

    Never raises for the ordinary failures — an unreachable server, a clean
    tree, an interrupted rebase, a rejected push — because the daemon calls this
    in a loop and one bad repository must not stop the others.

    `quiet_seconds` is the daemon's patience: a dirty repository whose files
    were touched more recently than that is left alone until the next sweep,
    on the grounds that the work is still in progress. `suwgit commit` passes
    0 — asking for a commit by hand is the statement that you are done.

    Anything that really blocks a repository — an unreachable model, a
    suspected secret, a rejected push — is also written into `.suwgit.log` in
    the repository itself, where it is in front of you while you work there.
    The note is deleted again the moment there is nothing left to do — whether
    suwgit committed, or you committed by hand.

    `push` overrides the config for this one call; None means follow the config.
    A failed push never undoes the commit: the work is safe locally either way,
    and the next sweep pushes it along with whatever comes next.
    """
    log = logger()
    should_push = config.push if push is None else push

    try:
        with repo_lock(root):
            interrupted = gitops.in_progress_operation(root)
            if interrupted:
                log.info("%s: skipped, %s in progress", root, interrupted)
                return Result(root, False, f"{interrupted} in progress")

            tree = gitops.read_working_tree(root, config.max_diff_chars)

            if tree.is_dirty:
                idle = gitops.seconds_since_last_change(root, tree.status)
                if quiet_seconds and idle is not None and idle < quiet_seconds:
                    log.info(
                        "%s: still being edited (%.0f min ago, waiting for %.0f), leaving it for the next sweep",
                        root,
                        idle / 60,
                        quiet_seconds / 60,
                    )
                    return Result(root, False, "changes are still warm", deferred=True)

                suggestion = suggest_commit_message(config.llm, tree)

                if suggestion.unsafe:
                    # Refusing is the whole point: a secret in a git history is
                    # not undone by a later commit, and the daemon commits
                    # unattended, so this is the only moment anyone can stop it.
                    reason = suggestion.unsafe_reason or "no reason given"
                    log.warning("%s: REFUSED to commit — possible secret in the changes (%s)", root, reason)
                    repolog.record_blocker(root, f"refused to commit — possible secret in the changes: {reason}")
                    return Result(root, False, f"possible secret in the changes: {reason}", unsafe=True)

                message = suggestion.message
                commit = gitops.commit_all(root, message)
                log.info("%s: committed %s %s", root, commit, message)
                repolog.clear(root)
                result = Result(root, True, "committed", message=message, commit=commit)
            else:
                pending = gitops.pending_commits(root) if should_push else 0
                if not pending:
                    # Nothing left to do is also what a repository looks like
                    # after you committed it yourself: whatever blocked suwgit
                    # is over, so the note must not outlive it.
                    log.info("%s: nothing to commit", root)
                    repolog.clear(root)
                    return Result(root, False, "nothing to commit")
                log.info("%s: nothing to commit, but %d commit(s) not pushed yet", root, pending)
                result = Result(root, False, f"{pending} commit(s) waiting to be pushed")

            if not should_push:
                return result

            try:
                result.pushed = gitops.push(root)
            except gitops.GitError as exc:
                result.push_error = str(exc)
                log.warning("%s: could not push (%s)", root, exc)
                repolog.record_blocker(root, f"could not push: {exc}")
                return result

            log.info("%s: %s", root, result.pushed)
            repolog.clear(root)
            return result

    except Busy as exc:
        log.info("%s: %s", root, exc)
        return Result(root, False, str(exc))
    except LlmUnavailable as exc:
        log.warning("%s: LLM unavailable, leaving changes uncommitted (%s)", root, exc)
        repolog.record_blocker(root, f"LLM unavailable, changes left uncommitted: {exc}")
        return Result(root, False, f"LLM unavailable: {exc}")
    except gitops.GitError as exc:
        log.error("%s: git failed (%s)", root, exc)
        repolog.record_blocker(root, f"git failed: {exc}")
        return Result(root, False, f"git failed: {exc}")
