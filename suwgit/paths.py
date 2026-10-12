"""Where suwgit keeps its state on disk.

XDG locations, so nothing lands in the repo and a daemon started by systemd
finds exactly the same files as an interactive shell.
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
# The vLLM key never goes into the dotfiles repo — it stays here, chmod 600.
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

# ~/.dotfiles is a stow tree: the `fedora` package is the one that gets stowed on
# this machine, and suwgit (systemd, user units) is Fedora-only anyway.
DOTFILES_DIR = Path(os.environ.get("DOTFILES", Path.home() / ".dotfiles")).expanduser()
DOTFILES_PACKAGE = "fedora"
DOTFILES_CONFIG_FILE = DOTFILES_DIR / DOTFILES_PACKAGE / ".config" / "suwgit" / "config.json"
DOTFILES_APPLY = Path.home() / "scripts" / "fedora" / "dotfiles" / "apply.sh"

BIN_DIR = Path.home() / ".local" / "bin"
BIN_LINK = BIN_DIR / "suwgit"

SYSTEMD_USER_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "systemd" / "user"
SERVICE_NAME = "suwgit.service"
SERVICE_FILE = SYSTEMD_USER_DIR / SERVICE_NAME

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "bin" / "suwgit"
