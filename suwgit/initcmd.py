"""`suwgit init` — the interactive setup.

Asks where the config should live, for the vLLM endpoint, how often to sweep,
and whether to start with the session; then puts suwgit on PATH and installs the
systemd user unit. Re-runnable: every prompt offers the current value as default.
"""

from __future__ import annotations

from pathlib import Path

from . import config as config_module
from . import install, paths
from .config import DEFAULT_INTERVAL_HOURS, Config

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _ask(prompt: str, default: str = "", allow_empty: bool = False) -> str:
    suffix = f" {DIM}[{default}]{RESET}" if default else ""
    while True:
        answer = input(f"{prompt}{suffix}: ").strip() or default
        if answer or allow_empty:
            return answer
        print(f"  {YELLOW}a value is required{RESET}")


def _ask_hours(default: float) -> float:
    while True:
        raw = _ask("How often to sweep, in hours", f"{default:g}")
        try:
            hours = float(raw.replace(",", "."))
        except ValueError:
            print(f"  {YELLOW}hours only — a number like 1.5{RESET}")
            continue
        if hours <= 0:
            print(f"  {YELLOW}must be greater than zero{RESET}")
            continue
        return hours


def _ask_yes_no(prompt: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} {DIM}[{hint}]{RESET}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print(f"  {YELLOW}answer y or n{RESET}")


def _ask_location() -> bool:
    """True for dotfiles, False for the home directory. Defaults to whatever is in place."""
    default_dotfiles = paths.CONFIG_FILE.is_symlink() or paths.DOTFILES_CONFIG_FILE.exists()
    print(f"{BOLD}Config location{RESET}")
    print(f"  1) dotfiles  {DIM}{paths.DOTFILES_CONFIG_FILE}{RESET}  (stowed to ~/.config)")
    print(f"  2) home      {DIM}{paths.CONFIG_FILE}{RESET}")
    default = "1" if default_dotfiles else "2"
    while True:
        raw = _ask("  Choose", default)
        if raw in ("1", "dotfiles"):
            return True
        if raw in ("2", "home"):
            return False
        print(f"  {YELLOW}pick 1 or 2{RESET}")


def _place_config(config: Config, use_dotfiles: bool) -> tuple[Path, str]:
    """Write the config to the chosen home, tidying up whatever the other mode left."""
    if use_dotfiles:
        target = config_module.save(config, paths.DOTFILES_CONFIG_FILE)
        if paths.CONFIG_FILE.exists() and not paths.CONFIG_FILE.is_symlink():
            # A real file here would make `stow` refuse to lay down its symlink.
            paths.CONFIG_FILE.unlink()
            return target, f"config written to {target} (the old {paths.CONFIG_FILE} was removed so stow can link it)"
        return target, f"config written to {target}"

    if paths.CONFIG_FILE.is_symlink():
        paths.CONFIG_FILE.unlink()
    target = config_module.save(config, paths.CONFIG_FILE)
    return target, f"config written to {target}"


def run() -> int:
    existing = config_module.load_or_default()

    print(f"\n{BOLD}suwgit init{RESET}\n")
    use_dotfiles = _ask_location()

    print(f"\n{BOLD}vLLM server{RESET}")
    base_url = _ask("  Base URL (OpenAI-compatible, e.g. http://localhost:8000/v1)", existing.llm.base_url)
    model = _ask("  Model name", existing.llm.model)
    api_key = _ask("  API key (blank if the server does not check one)", existing.llm.api_key, allow_empty=True)

    print(f"\n{BOLD}Daemon{RESET}")
    interval_hours = _ask_hours(existing.interval_hours or DEFAULT_INTERVAL_HOURS)
    open_on_system_start = _ask_yes_no("  Start with the system?", existing.open_on_system_start)
    push = _ask_yes_no("  Push the branch after committing?", existing.push)

    config = Config(
        llm=config_module.LlmConfig(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=api_key,
            timeout_seconds=existing.llm.timeout_seconds,
        ),
        interval_hours=interval_hours,
        open_on_system_start=open_on_system_start,
        push=push,
        max_diff_chars=existing.max_diff_chars,
        repos=existing.repos,
    )
    _, detail = _place_config(config, use_dotfiles)
    print(f"\n{GREEN}✓{RESET} {detail}")
    if api_key:
        print(f"{GREEN}✓{RESET} API key kept out of the config, in {paths.API_KEY_FILE} (chmod 600)")

    linked, detail = install.link_into_path()
    print(f"{GREEN}✓{RESET} {detail}" if linked else f"{YELLOW}!{RESET} {detail}")

    installed, detail = install.install_service(open_on_system_start)
    print(f"{GREEN}✓{RESET} {detail}" if installed else f"{YELLOW}!{RESET} {detail}")

    print(f"\n{BOLD}Next{RESET}")
    if not install.path_contains_local_bin():
        print(f"  {YELLOW}!{RESET} {paths.BIN_DIR} is not on your PATH — add it to your shell rc first:")
        print('      export PATH="$HOME/.local/bin:$PATH"')

    step = 1
    if use_dotfiles:
        print(f"  {step}. {BOLD}Apply the dotfiles{RESET} so ~/.config/suwgit is stowed:  {paths.DOTFILES_APPLY}")
        step += 1
    print(f"  {step}. {BOLD}Open a new terminal{RESET} so `suwgit` is picked up from PATH.")
    print(f"  {step + 1}. Register a repository:  suwgit register <folder>")
    print(f"  {step + 2}. Watch it work:          suwgit logs -f\n")
    return 0
