"""Linux: suwgit on PATH as a symlink, and a systemd user unit that runs `suwgit daemon`.

The other half of the seam, beside `install_windows.py`; `install.py` picks one.
Both steps are idempotent: `suwgit init` may be re-run whenever the config
needs changing, and it must not pile up symlinks or duplicate units.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import paths

AUTOSTART_QUESTION = "Start with the system?"

PATH_HINT = (
    "is not on your PATH — add it to your shell rc first:",
    '    export PATH="$HOME/.local/bin:$PATH"',
)

SERVICE_TEMPLATE = """[Unit]
Description=suwgit — commits registered repositories with LLM-written messages
After=network-online.target

[Service]
Type=simple
Environment="PATH={path}"
ExecStart={entrypoint} daemon
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
"""


def render_service() -> str:
    """The unit, with the PATH of the shell running `suwgit init` baked in.

    Commits run the repositories' git hooks (gitleaks, linters), which live
    wherever the user installed them — ~/.nix-profile/bin, ~/.local/bin. The
    systemd user manager's own PATH need not include those, and a daemon only
    ever sees the PATH it started with, so a hook's tool goes missing and the
    commit is refused.
    """
    path = os.environ.get("PATH", "/usr/local/bin:/usr/bin").replace("%", "%%")
    return SERVICE_TEMPLATE.format(entrypoint=paths.ENTRYPOINT, path=path)


def link_into_path() -> tuple[bool, str]:
    """Symlink ~/.local/bin/suwgit at the repo entrypoint."""
    paths.BIN_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.ENTRYPOINT
    target.chmod(0o755)

    if paths.BIN_LINK.is_symlink() and paths.BIN_LINK.resolve() == target.resolve():
        return True, f"already on PATH: {paths.BIN_LINK} → {target}"
    if paths.BIN_LINK.exists() and not paths.BIN_LINK.is_symlink():
        return False, f"{paths.BIN_LINK} exists and is not a symlink — remove it and re-run `suwgit init`"

    paths.BIN_LINK.unlink(missing_ok=True)
    paths.BIN_LINK.symlink_to(target)
    return True, f"linked {paths.BIN_LINK} → {target}"


def launchers() -> list[Path]:
    """The symlink, if it is there, for `uninstall` to list."""
    return [paths.BIN_LINK] if paths.BIN_LINK.is_symlink() else []


def schedule_label() -> str:
    """What `uninstall` lists as the scheduler's part, or "" when there is no unit."""
    return f"{paths.SERVICE_FILE}  (stopped and disabled first)" if paths.SERVICE_FILE.exists() else ""


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        done = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        return 1, str(exc)
    return done.returncode, (done.stderr or done.stdout).strip()


def install_schedule(_interval_hours: float, enable: bool) -> tuple[bool, str]:
    """Write the user unit, then enable/start it or stop/disable it."""
    if shutil.which("systemctl") is None:
        return False, "systemctl not found — autostart skipped, run `suwgit daemon` yourself"

    paths.SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    paths.SERVICE_FILE.write_text(render_service(), encoding="utf-8")
    _systemctl("daemon-reload")

    if not enable:
        _systemctl("disable", "--now", paths.SERVICE_NAME)
        return True, f"wrote {paths.SERVICE_FILE}, autostart off (start by hand: systemctl --user start {paths.SERVICE_NAME})"

    code, detail = _systemctl("enable", "--now", paths.SERVICE_NAME)
    if code != 0:
        return False, f"wrote {paths.SERVICE_FILE}, but enabling failed: {detail}"
    return True, f"{paths.SERVICE_NAME} enabled and running (starts with your session)"


def uninstall_schedule() -> list[str]:
    """Stop, disable and delete the user unit. Returns what was actually done."""
    done = []
    if shutil.which("systemctl") is not None:
        code, _ = _systemctl("disable", "--now", paths.SERVICE_NAME)
        if code == 0:
            done.append(f"stopped and disabled {paths.SERVICE_NAME}")
    if paths.SERVICE_FILE.exists():
        paths.SERVICE_FILE.unlink()
        done.append(f"removed {paths.SERVICE_FILE}")
        if shutil.which("systemctl") is not None:
            _systemctl("daemon-reload")
    return done


def unlink_from_path() -> list[str]:
    """Remove the PATH symlink — but never a real file someone else put there."""
    if paths.BIN_LINK.is_symlink():
        target = paths.BIN_LINK.resolve()
        paths.BIN_LINK.unlink()
        return [f"removed {paths.BIN_LINK} (was → {target})"]
    if paths.BIN_LINK.exists():
        return [f"left {paths.BIN_LINK} alone — it is a real file, not our symlink"]
    return []


def schedule_status() -> str:
    code, detail = _systemctl("is-active", paths.SERVICE_NAME)
    return detail or ("active" if code == 0 else "unknown")
