"""Reading and writing the suwgit config.

One flat file holds both the settings collected by `suwgit init` and the list
of registered repositories, so there is a single thing to back up or edit.

It is always READ from ~/.config/suwgit/config.json. In dotfiles mode that path
is a stow symlink into ~/.dotfiles, so writes follow the symlink and land in the
dotfiles repo — `register` keeps working without a second `stow`.

The API key is the one thing that never goes in there: the config may be a
tracked file in a git repo, so the key lives beside the logs in ~/.local/state.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import language as lang
from . import paths

DEFAULT_INTERVAL_HOURS = 1.5
# A thinking model reasons before it answers: qwen3-8b takes ~30 s on a small diff.
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_DIFF_CHARS = 400_000
# A sweep leaves a repository alone while its files are still being touched.
# Committing mid-edit yields half-finished commits described by a message
# written about work that is not done yet. Three quarters of the interval, so a
# repository that falls quiet just after one sweep is still caught by the next.
QUIET_FRACTION = 0.75


@dataclass
class LlmConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)


@dataclass
class MailConfig:
    """One mailbox `suwgit report --mail` can send from, known by its sender address."""

    host: str = ""
    port: int = 587
    # "starttls" (port 587, most providers), "ssl" (port 465), or "none" for a
    # relay on a trusted network.
    security: str = "starttls"
    username: str = ""
    password: str = ""  # never written to the config — see write_mail_passwords
    sender: str = ""  # the From address; the username when that is an address

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.sender)


@dataclass
class RepoEntry:
    """One registered repository and how suwgit treats it."""

    path: str
    # Swept by the daemon. Off for a repository you only want in reports, and
    # commit yourself with `suwgit commit`.
    auto_commit: bool = True
    # What a bare `--b2b` / `--project` on `suwgit commit` and `push` stand for
    # here. A plain `suwgit commit` stays ordinary; the daemon's sweeps, with
    # auto_commit on, commit B2B under these whenever a tenant is set.
    default_tenant: str = ""
    default_project: str = ""
    # The language its commits are written in. Set from the config's default
    # when the repository is registered; empty in an entry from before languages,
    # which then follows the default.
    language: str = ""


@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    interval_hours: float = DEFAULT_INTERVAL_HOURS
    open_on_system_start: bool = True
    # Push the branch after committing. Off by default: committing for you is
    # one thing, publishing on your behalf is another.
    push: bool = False
    # How much diff the model may see. Raise it for a big context window, lower
    # it for a small one; the whole diff is sent when it fits.
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS
    # "Prepared by" on `suwgit report`. Empty means the commits' author names,
    # which are often a handle rather than the name a client should read.
    report_author: str = ""
    # What a repository registered without --language, or not registered at
    # all, writes its commits in.
    language: str = lang.DEFAULT
    # The mailboxes reports can be sent from: none until `init` or `add-mail`.
    mailboxes: list[MailConfig] = field(default_factory=list)
    repos: list[RepoEntry] = field(default_factory=list)

    def entry(self, root: Path) -> RepoEntry | None:
        """The registry entry for `root`, if it is registered."""
        return next((e for e in self.repos if e.path == str(root)), None)

    def language_of(self, root: Path) -> str:
        """The language commits in `root` are written in."""
        entry = self.entry(root)
        return (entry.language if entry else "") or self.language

    @property
    def interval_seconds(self) -> float:
        return self.interval_hours * 3600

    @property
    def quiet_seconds(self) -> float:
        """How long a repository must sit untouched before a sweep commits it."""
        return self.interval_seconds * QUIET_FRACTION


class ConfigError(Exception):
    """Config file is missing or unreadable."""


def read_api_key() -> str:
    """The key from ~/.local/state/suwgit/api_key, or empty if there is none."""
    try:
        return paths.API_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_api_key(api_key: str) -> None:
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not api_key:
        paths.API_KEY_FILE.unlink(missing_ok=True)
        return
    paths.API_KEY_FILE.write_text(api_key + "\n", encoding="utf-8")
    os.chmod(paths.API_KEY_FILE, 0o600)


def read_mail_passwords() -> dict[str, str]:
    """Sender address → password, from ~/.local/state/suwgit/mail_passwords.json."""
    try:
        raw = json.loads(paths.MAIL_PASSWORDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def write_mail_passwords(mailboxes: list[MailConfig]) -> None:
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    passwords = {m.sender: m.password for m in mailboxes if m.password}
    if not passwords:
        paths.MAIL_PASSWORDS_FILE.unlink(missing_ok=True)
        return
    paths.MAIL_PASSWORDS_FILE.write_text(json.dumps(passwords, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(paths.MAIL_PASSWORDS_FILE, 0o600)


def source_file() -> Path:
    """The config file in force: the stowed one, or the dotfiles copy if stow has not run yet."""
    if paths.CONFIG_FILE.exists():
        return paths.CONFIG_FILE
    if paths.DOTFILES_CONFIG_FILE.exists():
        return paths.DOTFILES_CONFIG_FILE
    return paths.CONFIG_FILE


def write_target() -> Path:
    """Where a save actually writes — through the stow symlink when there is one."""
    current = source_file()
    return current.resolve() if current.is_symlink() else current


def load() -> Config:
    """Read the config, falling back to defaults for anything not written yet."""
    current = source_file()
    try:
        raw = json.loads(current.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"no config at {current} — run `suwgit init` first") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {current}: {exc}") from None

    llm_raw = raw.get("llm") or {}
    return Config(
        llm=LlmConfig(
            base_url=str(llm_raw.get("base_url", "")).rstrip("/"),
            model=str(llm_raw.get("model", "")),
            api_key=read_api_key(),
            timeout_seconds=int(llm_raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        ),
        interval_hours=float(raw.get("interval_hours", DEFAULT_INTERVAL_HOURS)),
        open_on_system_start=bool(raw.get("open_on_system_start", True)),
        push=bool(raw.get("push", False)),
        max_diff_chars=int(raw.get("max_diff_chars", DEFAULT_MAX_DIFF_CHARS)),
        report_author=str(raw.get("report_author", "")),
        language=_language(raw.get("language")) or lang.DEFAULT,
        mailboxes=_mailboxes(raw),
        repos=[_repo_entry(item) for item in raw.get("repos", [])],
    )


def _mailboxes(raw: dict) -> list[MailConfig]:
    """Every mailbox, with its password. A config from before several mailboxes has one, as `mail`."""
    items = raw.get("mailboxes")
    if items is None:
        items = [raw["mail"]] if isinstance(raw.get("mail"), dict) else []
    passwords = read_mail_passwords()
    boxes = []
    for item in items:
        box = MailConfig(
            host=str(item.get("host", "")),
            port=int(item.get("port", 587)),
            security=str(item.get("security", "starttls")),
            username=str(item.get("username", "")),
            sender=str(item.get("sender", "")),
        )
        box.password = passwords.get(box.sender, "")
        if not box.password and "mailboxes" not in raw:
            # Saved by the version with one mailbox, in a file of its own.
            try:
                box.password = (paths.STATE_DIR / "mail_password").read_text(encoding="utf-8").rstrip("\n")
            except OSError:
                pass
        if box.is_configured:
            boxes.append(box)
    return boxes


def mailbox(config: Config, sender: str) -> MailConfig | None:
    """The mailbox sending as `sender`, matched without regard to case."""
    return next((m for m in config.mailboxes if m.sender.lower() == sender.strip().lower()), None)


def add_mailbox(config: Config, box: MailConfig) -> str:
    """Add a mailbox, or replace the one with the same sender. Returns "added" or "updated"."""
    existing = mailbox(config, box.sender)
    if existing is None:
        config.mailboxes.append(box)
        return "added"
    config.mailboxes[config.mailboxes.index(existing)] = box
    return "updated"


def remove_mailbox(config: Config, sender: str) -> bool:
    existing = mailbox(config, sender)
    if existing is None:
        return False
    config.mailboxes.remove(existing)
    return True


def _repo_entry(item) -> RepoEntry:
    """A registry entry, from either shape: a bare path (older configs) or an object."""
    if isinstance(item, str):
        return RepoEntry(path=item)
    return RepoEntry(
        path=str(item["path"]),
        auto_commit=bool(item.get("auto_commit", True)),
        default_tenant=str(item.get("default_tenant", "")),
        default_project=str(item.get("default_project", "")),
        language=_language(item.get("language")),
    )


def _language(value) -> str:
    """A language code from the file, or "" for one missing or unknown — never a crash over a typo."""
    try:
        return lang.check(str(value)) if value else ""
    except lang.LanguageError:
        return ""


def load_or_default() -> Config:
    """Like load(), but a missing file is not an error — used by `init`."""
    try:
        return load()
    except ConfigError:
        return Config()


def save(config: Config, path: Path | None = None) -> Path:
    """Write the config atomically, keeping the API key and the mail password out of the file."""
    target = path or write_target()
    target.parent.mkdir(parents=True, exist_ok=True)

    document = asdict(config)
    document["llm"]["api_key"] = ""
    for box in document["mailboxes"]:
        box.pop("password")
    write_api_key(config.llm.api_key)
    write_mail_passwords(config.mailboxes)

    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def register(
    config: Config,
    repo_root: Path,
    auto_commit: bool | None = None,
    default_tenant: str | None = None,
    default_project: str | None = None,
    language: str | None = None,
) -> str:
    """Add a repository to the registry, or change the settings of one already there.

    A setting left as None keeps what the entry has (or the default, for a new
    one), so registering again with one flag changes that flag only. An empty
    string clears a default. A new entry without a `language` takes the
    config's default, and keeps it when the default changes later.
    Returns "added", "updated" or "unchanged".
    """
    entry = config.entry(repo_root)
    status = "updated"
    if entry is None:
        entry = RepoEntry(path=str(repo_root), language=config.language)
        config.repos.append(entry)
        config.repos.sort(key=lambda e: e.path)
        status = "added"

    before = (entry.auto_commit, entry.default_tenant, entry.default_project, entry.language)
    if auto_commit is not None:
        entry.auto_commit = auto_commit
    if default_tenant is not None:
        entry.default_tenant = default_tenant
    if default_project is not None:
        entry.default_project = default_project.strip()
    if language is not None:
        entry.language = language
    if status == "updated" and (entry.auto_commit, entry.default_tenant, entry.default_project, entry.language) == before:
        return "unchanged"
    return status


def unregister(config: Config, repo_root: Path) -> bool:
    """Drop a repository from the registry. False if it was not registered."""
    entry = config.entry(repo_root)
    if entry is None:
        return False
    config.repos.remove(entry)
    return True
