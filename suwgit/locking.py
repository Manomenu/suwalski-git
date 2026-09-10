"""One commit at a time per repository.

The daemon wakes up on its own schedule, so it can land on a repository at the
same moment `suwgit commit` is working on it. Both take this lock; whoever is
second walks away instead of racing over the index.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
from collections.abc import Iterator
from pathlib import Path

from . import paths


class Busy(Exception):
    """Another suwgit process holds this repository."""


@contextlib.contextmanager
def repo_lock(root: Path) -> Iterator[None]:
    paths.LOCK_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    lock_file = paths.LOCK_DIR / f"{digest}.lock"

    with lock_file.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(f"another suwgit run is already working on {root}") from None
        handle.write(str(root))
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
