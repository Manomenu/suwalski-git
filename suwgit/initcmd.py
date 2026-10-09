"""`suwgit init` — the interactive setup behind `just install`.

Asks for the vLLM endpoint, how often to sweep, whether the schedule is on and
whether to push; then puts suwgit on PATH and registers the scheduled task.
Re-runnable: every prompt offers the current value as default.
"""

from __future__ import annotations

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
        raw = _ask("  How often to sweep, in hours", f"{default:g}")
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


def run() -> int:
    existing = config_module.load_or_default()

    print(f"\n{BOLD}suwgit init{RESET}  {DIM}config: {paths.CONFIG_FILE}{RESET}\n")
    print(f"{BOLD}vLLM server{RESET}")
    base_url = _ask("  Base URL (OpenAI-compatible, e.g. http://localhost:8000/v1)", existing.llm.base_url)
    model = _ask("  Model name", existing.llm.model)
    api_key = _ask("  API key (blank if the server does not check one)", existing.llm.api_key, allow_empty=True)

    print(f"\n{BOLD}Schedule{RESET}")
    interval_hours = _ask_hours(existing.interval_hours or DEFAULT_INTERVAL_HOURS)
    scheduled = _ask_yes_no("  Sweep on a schedule (Task Scheduler)?", existing.scheduled)
    push = _ask_yes_no("  Push the branch after committing?", existing.push)

    config = Config(
        llm=config_module.LlmConfig(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=api_key,
            timeout_seconds=existing.llm.timeout_seconds,
        ),
        interval_hours=interval_hours,
        scheduled=scheduled,
        push=push,
        max_diff_chars=existing.max_diff_chars,
        repos=existing.repos,
    )
    target = config_module.save(config)
    print(f"\n{GREEN}✓{RESET} config written to {target}")
    if api_key:
        print(f"{GREEN}✓{RESET} API key kept out of the config, in {paths.API_KEY_FILE}")

    linked, detail = install.link_into_path()
    print(f"{GREEN}✓{RESET} {detail}" if linked else f"{YELLOW}!{RESET} {detail}")

    installed, detail = install.install_task(interval_hours, scheduled)
    print(f"{GREEN}✓{RESET} {detail}" if installed else f"{YELLOW}!{RESET} {detail}")

    print(f"\n{BOLD}Next{RESET}")
    if not install.path_contains_local_bin():
        print(f"  {YELLOW}!{RESET} {paths.BIN_DIR} is not on your PATH — add it to the user PATH in Windows")
        print("      (or in Git Bash: echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bashrc)")
    print(f"  1. {BOLD}Open a new terminal{RESET} so `suwgit` is picked up from PATH.")
    print("  2. Register a repository:  suwgit register <folder>")
    print("  3. Watch it work:          suwgit logs -f\n")
    return 0
