"""`suwgit uninstall` — undo what `init` put on this machine.

Deliberately reluctant: it lists every path first and the confirmation defaults
to no. Your **config is kept** unless you ask for `--purge`, so uninstalling and
reinstalling does not cost you the vLLM settings or the list of registered
repositories. It never touches the cloned repository, and never a commit suwgit
has already made.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import install, paths

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _exists(path: Path) -> bool:
    # is_symlink() first: a dangling stow symlink still needs removing.
    return path.is_symlink() or path.exists()


def runtime_targets() -> list[Path]:
    """What suwgit generated while running: logs and locks. Always removed."""
    log_lock = paths.LOG_FILE.with_suffix(paths.LOG_FILE.suffix + ".lock")
    return [path for path in (paths.LOG_FILE, log_lock, paths.LOCK_DIR) if _exists(path)]


def config_targets() -> list[Path]:
    """What you configured: the config itself and the API key. Only on --purge."""
    candidates = (paths.CONFIG_FILE, paths.DOTFILES_CONFIG_FILE, paths.API_KEY_FILE, paths.CONFIG_DIR, paths.STATE_DIR)
    return [path for path in candidates if _exists(path)]


def remove(paths_to_remove: list[Path]) -> list[str]:
    removed = []
    for path in paths_to_remove:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                continue
        except OSError as exc:
            removed.append(f"{YELLOW}!{RESET} could not remove {path}: {exc}")
            continue
        removed.append(f"removed {path}")
    return removed


def run(purge: bool = False, assume_yes: bool = False) -> int:
    doomed = runtime_targets() + (config_targets() if purge else [])
    installed = paths.BIN_LINK.is_symlink() or paths.SERVICE_FILE.exists()

    if not doomed and not installed:
        print(f"\n{DIM}nothing to do — suwgit is not installed on this machine{RESET}\n")
        return 0

    print(f"\n{BOLD}suwgit uninstall{RESET} — this will remove:\n")
    if paths.BIN_LINK.is_symlink():
        print(f"  {paths.BIN_LINK}  {DIM}(PATH symlink){RESET}")
    if paths.SERVICE_FILE.exists():
        print(f"  {paths.SERVICE_FILE}  {DIM}(stopped and disabled first){RESET}")
    for path in doomed:
        print(f"  {path}")

    kept = [] if purge else [path for path in config_targets() if path in (paths.CONFIG_FILE, paths.DOTFILES_CONFIG_FILE)]
    if kept:
        print(f"\n{BOLD}Kept{RESET} {DIM}(pass --purge to remove these too){RESET}:")
        for path in kept:
            print(f"  {path}")
    if purge and paths.DOTFILES_CONFIG_FILE in doomed:
        print(f"\n{YELLOW}!{RESET} {paths.DOTFILES_CONFIG_FILE} is inside your dotfiles repo —")
        print("  removing it leaves a deletion for you to commit there.")

    print(f"\n{DIM}The clone at {paths.REPO_ROOT} is left alone, and so is every commit suwgit made.{RESET}")

    if not assume_yes:
        answer = input(f"\n{BOLD}Remove all of it?{RESET} {DIM}[y/N]{RESET}: ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted, nothing removed")
            return 1

    print()
    for line in install.uninstall_service() + install.unlink_from_path() + remove(doomed):
        print(f"  {line}" if line.startswith(YELLOW) else f"  {GREEN}✓{RESET} {line}")

    tail = "" if purge else " Your config is still there — `suwgit init` picks it straight back up."
    print(f"\n{GREEN}✓{RESET} suwgit is uninstalled.{tail}\n")
    return 0
