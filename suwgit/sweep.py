"""One pass over the registered repositories — what the scheduled task runs.

The Linux version kept a daemon asleep between sweeps under systemd. On Windows
the Task Scheduler is the clock, so a sweep is a short process that starts,
commits the dirty repositories that have gone quiet, and exits. Silent by
construction: it writes to the log file and nowhere else.
"""

from __future__ import annotations

from pathlib import Path

from . import config as config_module
from .committer import commit_repo
from .logs import logger


def run() -> tuple[int, int, int]:
    """One sweep over the registry, as (committed, pushed, deferred).

    A repository with nothing to commit is still visited: if pushing is on and it
    has commits that never reached a remote, this is where they go out.

    A repository still being edited is deferred instead — see
    `Config.quiet_seconds`. Nothing is lost by waiting: the next sweep finds the
    same changes plus whatever was added in the meantime.
    """
    log = logger()
    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        log.error("sweep: %s", exc)
        return 0, 0, 0

    if not config.repos:
        log.info("sweep: no repositories registered")
        return 0, 0, 0

    committed = pushed = deferred = 0
    for entry in config.repos:
        root = Path(entry)
        if not (root / ".git").exists():
            log.warning("%s: registered but no longer a git repository, skipping", root)
            continue
        result = commit_repo(config, root, quiet_seconds=config.quiet_seconds)
        committed += bool(result.committed)
        pushed += bool(result.pushed)
        deferred += bool(result.deferred)

    log.info("sweep: done (%d committed, %d pushed, %d still being edited)", committed, pushed, deferred)
    return committed, pushed, deferred
