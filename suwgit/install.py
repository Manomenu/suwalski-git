"""Putting suwgit on PATH and under the system's scheduler.

The one place where Linux and Windows part ways in setup, together with `host.py`
(OS primitives): `install_systemd.py` (a symlink and a systemd user unit running
`suwgit daemon`) and `install_windows.py` (two launchers and a Task Scheduler entry
running `suwgit sweep`). This module hands every call to the one that fits the
system it runs on, so the rest of suwgit never asks which system that is.
Both implementations offer the same functions:

- `link_into_path()`, `unlink_from_path()`, `launchers()`
- `install_schedule(interval_hours, enabled)`, `uninstall_schedule()`,
  `schedule_status()`, `schedule_label()`
- `AUTOSTART_QUESTION` and `PATH_HINT`, the two texts of `suwgit init` that name the scheduler.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType

from . import host, install_systemd, install_windows, paths


def backend() -> ModuleType:
    return install_windows if host.is_windows() else install_systemd


def link_into_path() -> tuple[bool, str]:
    return backend().link_into_path()


def unlink_from_path() -> list[str]:
    return backend().unlink_from_path()


def launchers() -> list[Path]:
    return backend().launchers()


def install_schedule(interval_hours: float, enabled: bool) -> tuple[bool, str]:
    return backend().install_schedule(interval_hours, enabled)


def uninstall_schedule() -> list[str]:
    return backend().uninstall_schedule()


def schedule_status() -> str:
    return backend().schedule_status()


def schedule_label() -> str:
    return backend().schedule_label()


def autostart_question() -> str:
    return backend().AUTOSTART_QUESTION


def path_hint() -> tuple[str, ...]:
    return backend().PATH_HINT


def path_contains_local_bin() -> bool:
    def norm(p: str) -> str:
        return os.path.normcase(os.path.realpath(p))

    return norm(str(paths.BIN_DIR)) in {norm(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p}
