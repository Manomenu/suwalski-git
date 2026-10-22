"""One commit at a time per repository.

A scheduled sweep starts on its own clock, so it can land on a repository at the
same moment `suwgit commit` is working on it. Both take this lock; whoever is
second walks away instead of racing over the index.

The lock itself is the operating system's (`host.py`); it is dropped when the
process dies, so a crashed run never wedges a repository.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Iterator
from pathlib import Path

from . import host, paths


class Busy(Exception):
    """Another suwgit process holds this repository."""


@contextlib.contextmanager
def file_lock(path: Path, wait: bool) -> Iterator[None]:
    """Hold an exclusive lock on `path` (created if missing).

    `wait=False` raises Busy at once when someone else holds it; `wait=True`
    blocks until they let go.
    """
    with path.open("a+b") as handle:
        if not host.try_lock(handle, wait):
            raise Busy(f"{path} is locked by another suwgit process")
        try:
            yield
        finally:
            host.unlock(handle)


@contextlib.contextmanager
def repo_lock(root: Path) -> Iterator[None]:
    paths.LOCK_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    try:
        with file_lock(paths.LOCK_DIR / f"{digest}.lock", wait=False):
            yield
    except Busy:
        raise Busy(f"another suwgit run is already working on {root}") from None
