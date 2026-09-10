"""The daemon's only output channel.

Nothing running in the background may write to a terminal — an unreachable
vLLM server must be a log line, not a notification — so every code path that
the daemon touches reports through here.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from logging.handlers import RotatingFileHandler

from . import paths

MAX_BYTES = 2_000_000
BACKUP_COUNT = 3

_logger: logging.Logger | None = None


def logger() -> logging.Logger:
    """Rotating file logger at ~/.local/state/suwgit/suwgit.log."""
    global _logger
    if _logger is not None:
        return _logger

    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(paths.LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

    log = logging.getLogger("suwgit")
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(handler)
    _logger = log
    return log


def show(lines: int, follow: bool) -> int:
    """`suwgit logs` — the log through bat when it is installed, plain otherwise."""
    if not paths.LOG_FILE.exists():
        print(f"no log yet at {paths.LOG_FILE}")
        return 0

    if follow:
        return subprocess.call(["tail", "-n", str(lines), "-f", str(paths.LOG_FILE)])

    tail = subprocess.run(["tail", "-n", str(lines), str(paths.LOG_FILE)], capture_output=True, text=True, check=False)
    if tail.returncode != 0:
        sys.stderr.write(tail.stderr)
        return tail.returncode

    if sys.stdout.isatty():
        try:
            bat = subprocess.run(
                ["bat", "--language", "log", "--style", "plain", "--paging", "never", "--file-name", str(paths.LOG_FILE)],
                input=tail.stdout,
                text=True,
                check=False,
            )
            return bat.returncode
        except FileNotFoundError:
            pass

    sys.stdout.write(tail.stdout)
    return 0
