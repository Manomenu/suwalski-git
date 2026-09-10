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

from . import paths

DEFAULT_INTERVAL_HOURS = 1.5
# A thinking model reasons before it answers: qwen3-8b takes ~30 s on a small diff.
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_DIFF_CHARS = 400_000


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
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    interval_hours: float = DEFAULT_INTERVAL_HOURS
    open_on_system_start: bool = True
    # How much diff the model may see. Raise it for a big context window, lower
    # it for a small one; the whole diff is sent when it fits.
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS
    repos: list[str] = field(default_factory=list)

    @property
    def interval_seconds(self) -> float:
        return self.interval_hours * 3600


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
        max_diff_chars=int(raw.get("max_diff_chars", DEFAULT_MAX_DIFF_CHARS)),
        repos=[str(p) for p in raw.get("repos", [])],
    )


def load_or_default() -> Config:
    """Like load(), but a missing file is not an error — used by `init`."""
    try:
        return load()
    except ConfigError:
        return Config()


def save(config: Config, path: Path | None = None) -> Path:
    """Write the config atomically, keeping the API key out of the file."""
    target = path or write_target()
    target.parent.mkdir(parents=True, exist_ok=True)

    document = asdict(config)
    document["llm"]["api_key"] = ""
    write_api_key(config.llm.api_key)

    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def register(config: Config, repo_root: Path) -> bool:
    """Add a repository to the registry. False if it was already there."""
    entry = str(repo_root)
    if entry in config.repos:
        return False
    config.repos.append(entry)
    config.repos.sort()
    return True


def unregister(config: Config, repo_root: Path) -> bool:
    """Drop a repository from the registry. False if it was not registered."""
    entry = str(repo_root)
    if entry not in config.repos:
        return False
    config.repos.remove(entry)
    return True
