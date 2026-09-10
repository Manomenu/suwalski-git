"""Putting suwgit on PATH and under systemd.

Both steps are idempotent: `suwgit init` may be re-run whenever the config
needs changing, and it must not pile up symlinks or duplicate units.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from . import paths

SERVICE_TEMPLATE = """[Unit]
Description=suwgit — commits registered repositories with LLM-written messages
After=network-online.target

[Service]
Type=simple
ExecStart={entrypoint} daemon
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
"""


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


def path_contains_local_bin() -> bool:
    entries = {os.path.realpath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p}
    return os.path.realpath(paths.BIN_DIR) in entries


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        done = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        return 1, str(exc)
    return done.returncode, (done.stderr or done.stdout).strip()


def install_service(enable: bool) -> tuple[bool, str]:
    """Write the user unit, then enable/start it or stop/disable it."""
    if shutil.which("systemctl") is None:
        return False, "systemctl not found — autostart skipped, run `suwgit daemon` yourself"

    paths.SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    paths.SERVICE_FILE.write_text(SERVICE_TEMPLATE.format(entrypoint=paths.ENTRYPOINT), encoding="utf-8")
    _systemctl("daemon-reload")

    if not enable:
        _systemctl("disable", "--now", paths.SERVICE_NAME)
        return True, f"wrote {paths.SERVICE_FILE}, autostart off (start by hand: systemctl --user start {paths.SERVICE_NAME})"

    code, detail = _systemctl("enable", "--now", paths.SERVICE_NAME)
    if code != 0:
        return False, f"wrote {paths.SERVICE_FILE}, but enabling failed: {detail}"
    return True, f"{paths.SERVICE_NAME} enabled and running (starts with your session)"


def uninstall_service() -> list[str]:
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


def service_status() -> str:
    code, detail = _systemctl("is-active", paths.SERVICE_NAME)
    return detail or ("active" if code == 0 else "unknown")
