"""`suwgit init` — the interactive setup behind `just install`.

Asks for the vLLM endpoint, how often to sweep, whether the schedule is on,
whether to push and whose name goes on reports; then puts suwgit on PATH and
registers the scheduled task.
Re-runnable: every prompt offers the current value as default.
"""

from __future__ import annotations

import getpass

from . import config as config_module
from . import gitops, install, language, paths
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


def _ask_secret(prompt: str, existing: str) -> str:
    """A password, typed without echo; Enter keeps the one already saved."""
    hint = f" {DIM}[Enter keeps the saved one]{RESET}" if existing else ""
    return getpass.getpass(f"{prompt}{hint}: ") or existing


SECURITY_PORTS = {"starttls": 587, "ssl": 465, "none": 25}


def ask_mailbox(existing: config_module.MailConfig) -> config_module.MailConfig:
    """The questions for one mailbox; `existing` holds the defaults (blank for a new one).

    Also what `suwgit add-mail` asks.
    """
    sender = _ask("  Address — reports are sent from it", existing.sender)
    domain = sender.partition("@")[2]
    host = _ask("  SMTP server (e.g. smtp.gmail.com)", existing.host or (f"smtp.{domain}" if domain else ""))
    while True:
        security = _ask("  Encryption — starttls, ssl or none", existing.security or "starttls").lower()
        if security in SECURITY_PORTS:
            break
        print(f"  {YELLOW}starttls (port 587), ssl (port 465) or none{RESET}")
    default_port = existing.port if existing.host and existing.security == security else SECURITY_PORTS[security]
    while True:
        try:
            port = int(_ask("  Port", str(default_port)))
            break
        except ValueError:
            print(f"  {YELLOW}a number, like 587{RESET}")
    username = _ask("  Login (blank if the server needs none)", existing.username or sender, allow_empty=True)
    password = _ask_secret("  Password — Gmail and Outlook need an app password", existing.password) if username else ""
    return config_module.MailConfig(host=host, port=port, security=security, username=username, password=password, sender=sender)


def _ask_mailboxes(existing: list[config_module.MailConfig]) -> list[config_module.MailConfig]:
    """Optional: one more mailbox for `suwgit report --mail`. Enter leaves them as they are."""
    print(f"\n{BOLD}E-mail{RESET}  {DIM}optional: lets `suwgit report --mail <address>` send the PDF{RESET}")
    for box in existing:
        print(f"  {GREEN}✓{RESET} {box.sender}  {DIM}{box.host}:{box.port}{RESET}")
    if not _ask_yes_no("  Add a mailbox?", False):
        return list(existing)
    holder = config_module.Config(mailboxes=list(existing))
    config_module.add_mailbox(holder, ask_mailbox(config_module.MailConfig()))
    return holder.mailboxes


def _ask_language(default: str) -> str:
    """The default language of commits; repositories registered from now on take it."""
    choices = ", ".join(f"{code} ({language.name(code)})" for code in language.LANGUAGES)
    while True:
        try:
            return language.check(_ask(f"  Commits are written in — {choices}; register --language overrides it", default))
        except language.LanguageError as exc:
            print(f"  {exc}")


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

    print(f"\n{BOLD}Reports{RESET}")
    report_author = _ask(
        "  Your name on reports (Prepared by)",
        existing.report_author or gitops.global_user_name(),
        allow_empty=True,
    )
    mailboxes = _ask_mailboxes(existing.mailboxes)

    print(f"\n{BOLD}Language{RESET}")
    default_language = _ask_language(existing.language)

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
        report_author=report_author,
        language=default_language,
        mailboxes=mailboxes,
        repos=existing.repos,
    )
    target = config_module.save(config)
    print(f"\n{GREEN}✓{RESET} config written to {target}")
    if api_key:
        print(f"{GREEN}✓{RESET} API key kept out of the config, in {paths.API_KEY_FILE}")
    if any(box.password for box in mailboxes):
        print(f"{GREEN}✓{RESET} mail passwords kept out of the config, in {paths.MAIL_PASSWORDS_FILE}")

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
    print("  3. Watch it work:          suwgit logs -f")
    if mailboxes:
        print(f"  {DIM}Another mailbox to send reports from:  suwgit add-mail <address>{RESET}\n")
    else:
        print(f"  {DIM}To send reports by e-mail later:       suwgit add-mail <address>{RESET}\n")
    return 0
