"""The one operation suwgit performs: look at a dirty repository, ask the
model what happened there, commit under that name.

Shared by `suwgit commit` (foreground, prints) and the daemon (background,
logs only), so both make exactly the same decisions about what is committable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import gitops
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


def commit_repo(config: Config, root: Path) -> Result:
    """Commit everything in `root` under an LLM-written message.

    Never raises for the ordinary failures — an unreachable server, a clean
    tree, an interrupted rebase — because the daemon calls this in a loop and
    one bad repository must not stop the others.
    """
    log = logger()

    try:
        with repo_lock(root):
            interrupted = gitops.in_progress_operation(root)
            if interrupted:
                log.info("%s: skipped, %s in progress", root, interrupted)
                return Result(root, False, f"{interrupted} in progress")

            tree = gitops.read_working_tree(root, config.max_diff_chars)
            if not tree.is_dirty:
                log.info("%s: nothing to commit", root)
                return Result(root, False, "nothing to commit")

            message = suggest_commit_message(config.llm, tree)
            commit = gitops.commit_all(root, message)
    except Busy as exc:
        log.info("%s: %s", root, exc)
        return Result(root, False, str(exc))
    except LlmUnavailable as exc:
        log.warning("%s: LLM unavailable, leaving changes uncommitted (%s)", root, exc)
        return Result(root, False, f"LLM unavailable: {exc}")
    except gitops.GitError as exc:
        log.error("%s: git failed (%s)", root, exc)
        return Result(root, False, f"git failed: {exc}")

    log.info("%s: committed %s %s", root, commit, message)
    return Result(root, True, "committed", message=message, commit=commit)
