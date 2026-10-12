"""Which repositories hold B2B commits for which tenant.

A tenant is the working name of a client — `--b2b lokum`. `suwgit report
--tenant lokum` must find every repository that ever got a commit for it, also
ones registered for something else, or not registered at all, so each B2B
commit adds its repository here:

    {"lokum": ["/home/you/projects/dashboard", "/home/you/projects/shared"]}

It is only an index of where to look: the record of the work is the git
history, which this file never touches. A repository missing from it is added
back by its next B2B commit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import paths

TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class TenantError(Exception):
    """A tenant name that cannot be used as a key."""


def check(tenant: str) -> str:
    """The tenant name, if it is a usable key: letters, digits, `.`, `_`, `-`."""
    tenant = tenant.strip()
    if not TENANT_RE.match(tenant):
        raise TenantError(f"{tenant!r} is not a tenant name — use letters, digits, '.', '_' or '-', e.g. lokum")
    return tenant


def _read(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(path: Path, document: dict) -> None:
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load() -> dict[str, list[str]]:
    return {str(k): [str(p) for p in v] for k, v in _read(paths.TENANTS_FILE).items() if isinstance(v, list)}


def record(tenant: str, root: Path) -> None:
    """Note that `root` holds commits for `tenant`. Written atomically."""
    index = load()
    repos = index.setdefault(tenant, [])
    if str(root) in repos:
        return
    repos.append(str(root))
    repos.sort()
    _write(paths.TENANTS_FILE, index)


def repos_of(tenant: str) -> list[Path]:
    return [Path(p) for p in load().get(tenant, [])]


def last_used(root: Path) -> tuple[str, str] | None:
    """(tenant, project) of the last `--b2b` commit made by hand in `root`, if any."""
    last = _read(paths.LAST_B2B_FILE).get(str(root))
    if not isinstance(last, dict) or not last.get("tenant"):
        return None
    return str(last["tenant"]), str(last.get("project", ""))


def remember(root: Path, tenant: str, project: str) -> None:
    """Note what a `--b2b` commit by hand used in `root`, for the next bare `--b2b`."""
    document = _read(paths.LAST_B2B_FILE)
    document[str(root)] = {"tenant": tenant, "project": project}
    _write(paths.LAST_B2B_FILE, document)


def last_mail(tenant: str) -> tuple[list[str], str] | None:
    """(recipients, sender) of the last report mailed for `tenant`, if one was."""
    last = _read(paths.LAST_MAIL_FILE).get(tenant)
    if not isinstance(last, dict) or not last.get("recipients"):
        return None
    return [str(r) for r in last["recipients"]], str(last.get("sender", ""))


def remember_mail(tenant: str, recipients: list[str], sender: str) -> None:
    document = _read(paths.LAST_MAIL_FILE)
    document[tenant] = {"recipients": recipients, "sender": sender}
    _write(paths.LAST_MAIL_FILE, document)


def last_mails() -> dict[str, tuple[list[str], str]]:
    """Every tenant's last mailing, for `suwgit list`."""
    return {tenant: found for tenant in _read(paths.LAST_MAIL_FILE) if (found := last_mail(tenant))}
