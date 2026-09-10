"""The background loop started by systemd.

Wakes every `interval_hours`, walks the registered repositories, commits the
dirty ones. Silent by construction: it writes to the log file and nowhere else.
"""

from __future__ import annotations

import signal
import threading
from pathlib import Path

from . import config as config_module
from .committer import commit_repo
from .logs import logger

_stop = threading.Event()


def _handle_signal(signum, _frame) -> None:
    logger().info("daemon: signal %s, shutting down", signal.Signals(signum).name)
    _stop.set()


def run_once() -> tuple[int, int]:
    """One sweep over the registry, as (committed, pushed).

    A repository with nothing to commit is still visited: if pushing is on and it
    has commits that never reached a remote, this is where they go out.
    """
    log = logger()
    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        log.error("daemon: %s", exc)
        return 0, 0

    if not config.repos:
        log.info("daemon: no repositories registered")
        return 0, 0

    committed = pushed = 0
    for entry in config.repos:
        root = Path(entry)
        if not (root / ".git").exists():
            log.warning("%s: registered but no longer a git repository, skipping", root)
            continue
        result = commit_repo(config, root)
        committed += bool(result.committed)
        pushed += bool(result.pushed)
    return committed, pushed


def run() -> int:
    """Loop until systemd stops us. The interval is re-read every cycle."""
    log = logger()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("daemon: started")
    while not _stop.is_set():
        committed, pushed = run_once()
        interval = config_module.load_or_default().interval_seconds
        log.info("daemon: sweep done (%d committed, %d pushed), sleeping %.2f h", committed, pushed, interval / 3600)
        _stop.wait(interval)

    log.info("daemon: stopped")
    return 0
