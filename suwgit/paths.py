"""Where suwgit keeps its state on disk.

Under the user profile, in the same ~/.config and ~/.local/state a Git Bash user
already knows — and deliberately NOT under %APPDATA%: the Microsoft Store build
of Python silently redirects writes there into its own package folder, so the
file you open in an editor would not be the one suwgit reads.

A scheduled run of suwgit finds exactly the same files as an interactive shell,
because both resolve them from the same profile.
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    raw = os.environ.get(var)
    return Path(raw).expanduser() if raw else Path.home() / default


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "suwgit"
CONFIG_FILE = CONFIG_DIR / "config.json"

STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "suwgit"
LOG_FILE = STATE_DIR / "suwgit.log"
LOCK_DIR = STATE_DIR / "locks"
# The vLLM key never goes into the config file — it stays here, in the profile.
API_KEY_FILE = STATE_DIR / "api_key"
# The mailbox passwords `suwgit report --mail` sends with, by sender address,
# kept out of the config for the same reason.
MAIL_PASSWORDS_FILE = STATE_DIR / "mail_passwords.json"
# Where each tenant's last report was mailed, and from which mailbox.
LAST_MAIL_FILE = STATE_DIR / "last-mail.json"
# Which repositories ever got a B2B commit for which tenant, so a report knows
# where to look. An index only: the commits themselves are the record.
TENANTS_FILE = STATE_DIR / "tenants.json"
# The tenant and project of the last `--b2b` commit made by hand, per repository,
# so a bare `--b2b` there can offer them again.
LAST_B2B_FILE = STATE_DIR / "last-b2b.json"

# Two launchers, because Windows has two kinds of shell: Git Bash runs the
# extensionless script, cmd and PowerShell pick up the .cmd. Symlinks would need
# developer mode or an elevated prompt, so these are small generated files.
BIN_DIR = Path.home() / ".local" / "bin"
BASH_SHIM = BIN_DIR / "suwgit"
CMD_SHIM = BIN_DIR / "suwgit.cmd"

# The Task Scheduler entry that replaces the systemd unit of the Linux version.
TASK_NAME = "suwgit"

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "bin" / "suwgit.py"
