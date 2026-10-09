"""The only output channel of a scheduled sweep.

Nothing running in the background may write to a terminal — an unreachable
vLLM server must be a log line, not a notification — so every code path that
a scheduled sweep touches reports through here.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from collections import deque

from . import paths
from .locking import file_lock

# One capped file, no .1/.2/.3 siblings. When it fills up the oldest lines go.
MAX_BYTES = 5_000_000
# How much survives a trim. Half, so trimming happens rarely rather than on
# every line once the cap is reached.
KEEP_FRACTION = 0.5

_logger: logging.Logger | None = None


class CappedFileHandler(logging.Handler):
    """Append-only log with a hard size cap, safe for several processes.

    A scheduled sweep and an interactive `suwgit commit` write to the same file
    at the same time. `RotatingFileHandler` cannot survive that — one process renames
    the file while the other still holds the old inode open, and those lines are
    lost at the next rotation. So this handler opens the file fresh for every
    record (a handful per sweep — the cost is irrelevant) and takes a lock
    around the write, which makes the trim atomic for every writer.
    """

    def __init__(self, filename, max_bytes: int = MAX_BYTES) -> None:
        super().__init__()
        self.filename = filename
        self.max_bytes = max_bytes
        self.lock_file = filename.with_suffix(filename.suffix + ".lock")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record) + "\n"
            with file_lock(self.lock_file, wait=True):
                with self.filename.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                if self.filename.stat().st_size > self.max_bytes:
                    self._trim()
        except OSError:
            self.handleError(record)

    def _trim(self) -> None:
        """Drop the oldest lines, keeping the newest KEEP_FRACTION of the cap."""
        keep_bytes = int(self.max_bytes * KEEP_FRACTION)
        with self.filename.open("rb") as handle:
            handle.seek(-keep_bytes, os.SEEK_END)
            handle.readline()  # discard the half line we landed in the middle of
            tail = handle.read()

        tmp = self.filename.with_suffix(self.filename.suffix + ".trim")
        with tmp.open("wb") as handle:
            handle.write(b"... older entries dropped: the log is capped at %d bytes\n" % self.max_bytes)
            handle.write(tail)
        tmp.replace(self.filename)


def logger() -> logging.Logger:
    """Rotating file logger at ~/.local/state/suwgit/suwgit.log."""
    global _logger
    if _logger is not None:
        return _logger

    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    handler = CappedFileHandler(paths.LOG_FILE)
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

    log = logging.getLogger("suwgit")
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(handler)
    _logger = log
    return log


def _last_lines(count: int) -> str:
    with paths.LOG_FILE.open(encoding="utf-8", errors="replace") as handle:
        return "".join(deque(handle, maxlen=count))


def _follow() -> int:
    """`tail -f` in Python: cmd and PowerShell have no tail, and Git Bash's
    cannot be relied on to notice the in-place trim."""
    position = paths.LOG_FILE.stat().st_size
    try:
        while True:
            size = paths.LOG_FILE.stat().st_size
            if size < position:  # trimmed under us — start again from the top
                position = 0
            if size > position:
                with paths.LOG_FILE.open("rb") as handle:
                    handle.seek(position)
                    sys.stdout.write(handle.read().decode("utf-8", errors="replace"))
                    position = handle.tell()
                sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def show(lines: int, follow: bool) -> int:
    """`suwgit logs` — the log through bat when it is installed, plain otherwise."""
    if not paths.LOG_FILE.exists():
        print(f"no log yet at {paths.LOG_FILE}")
        return 0

    tail = _last_lines(lines)
    bat = shutil.which("bat")
    if bat and sys.stdout.isatty() and not follow:
        done = subprocess.run(
            [bat, "--language", "log", "--style", "plain", "--paging", "never", "--file-name", str(paths.LOG_FILE)],
            input=tail,
            text=True,
            encoding="utf-8",
            check=False,
        )
        return done.returncode

    sys.stdout.write(tail)
    sys.stdout.flush()
    return _follow() if follow else 0
