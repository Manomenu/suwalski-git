"""Where Linux and Windows differ, apart from the scheduler (see `install.py`).

Three small things, chosen at runtime by the OS:

- `no_window()`: the arguments every subprocess is started with. The scheduled
  sweep runs under `pythonw` with no console, and a child process that is not told
  so opens a console window on screen at every call.
- `lock()`: an exclusive lock on a file, for `locking.py`.
- `keep_private()`: a file of secrets readable by its owner only.

Everything else in suwgit is the same on both systems. `is_windows()` is a
function and not a constant, so a test can pretend to be the other system.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import IO, Any


def is_windows() -> bool:
    return sys.platform == "win32"


def no_window() -> dict[str, Any]:
    """Keyword arguments for `subprocess.run`: no console window on Windows, nothing elsewhere."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if is_windows() else {}


def keep_private(path: Path) -> None:
    """chmod 600. Windows ignores it, and a folder in the user profile is already readable only by its owner."""
    if not is_windows():
        os.chmod(path, 0o600)


def try_lock(handle: IO[bytes], wait: bool) -> bool:
    """Take an exclusive lock on the open file; False if `wait` is off and someone else holds it.

    Both kinds of lock are dropped by the OS when the process dies, so a crashed
    run never wedges a repository.
    """
    if is_windows():
        return _try_lock_windows(handle, wait)
    import fcntl

    try:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except OSError:
        return False
    return True


def unlock(handle: IO[bytes]) -> None:
    if is_windows():
        _unlock_windows(handle)
        return
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_UN)


# Windows has no flock: msvcrt.locking takes a byte-range lock instead. Its own
# blocking mode gives up after ten seconds, which is not "wait for the other
# writer", so a blocking lock polls.
POLL_SECONDS = 0.02


def _try_lock_windows(handle: IO[bytes], wait: bool) -> bool:
    import msvcrt
    import time

    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if not wait:
                return False
            time.sleep(POLL_SECONDS)
        else:
            return True


def _unlock_windows(handle: IO[bytes]) -> None:
    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
