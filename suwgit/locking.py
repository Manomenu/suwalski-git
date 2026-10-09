"""One commit at a time per repository.

A scheduled sweep starts on its own clock, so it can land on a repository at the
same moment `suwgit commit` is working on it. Both take this lock; whoever is
second walks away instead of racing over the index.

Windows has no flock: msvcrt.locking takes a byte-range lock instead, which the
OS also drops when the process dies, so a crashed run never wedges a repository.
"""

from __future__ import annotations

import contextlib
import hashlib
import msvcrt
import time
from collections.abc import Iterator
from pathlib import Path

from . import paths

# How often a blocking lock retries. msvcrt's own blocking mode gives up after
# ten seconds, which is not "wait for the other writer", so we poll instead.
POLL_SECONDS = 0.02


class Busy(Exception):
    """Another suwgit process holds this repository."""


@contextlib.contextmanager
def file_lock(path: Path, wait: bool) -> Iterator[None]:
    """Hold an exclusive lock on `path` (created if missing).

    `wait=False` raises Busy at once when someone else holds it; `wait=True`
    polls until they let go.
    """
    with path.open("a+b") as handle:
        fd = handle.fileno()
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if not wait:
                    raise Busy(f"{path} is locked by another suwgit process") from None
                time.sleep(POLL_SECONDS)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def repo_lock(root: Path) -> Iterator[None]:
    paths.LOCK_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    try:
        with file_lock(paths.LOCK_DIR / f"{digest}.lock", wait=False):
            yield
    except Busy:
        raise Busy(f"another suwgit run is already working on {root}") from None
