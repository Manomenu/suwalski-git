"""A chain of branches, each one's merge request aimed at the one before it (base <- b1 <- b2 ...).

The order is git-town's: the parent of a branch is `git-town-branch.<branch>.parent`,
which `git town append` and `git town set-parent` write. suwgit keeps no order of its
own, and merges nothing — merging up the chain is `git town sync --stack`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import gitops, mrs

ORIGIN = mrs.REMOTE


class ChainError(Exception):
    """The chain cannot be read from git."""


@dataclass(frozen=True)
class Link:
    branch: str
    parent: str | None  # None for the base
    pushed: bool
    ahead_of_origin: int
    behind_origin: int
    ahead_of_parent: int
    behind_parent: int  # the parent has this many commits the branch lacks: they need merging up


def read_chain(root: Path) -> list[str]:
    """The current branch and its git-town ancestors, base first."""
    branch = gitops.current_branch(root)
    if branch is None:
        raise ChainError("HEAD is detached — check out the last branch of the chain first")
    names = [branch]
    while parent := gitops.config_value(root, f"git-town-branch.{names[-1]}.parent"):
        if parent in names:
            raise ChainError(f"the git-town parents of {branch} go in a circle through {parent}")
        names.append(parent)
    if len(names) == 1:
        raise ChainError(
            f"{branch} has no git-town parent, so there is no chain to read — "
            f"set one with `git town set-parent` (on {branch}), or create the branches with `git town append <name>`"
        )
    return names[::-1]


def links(root: Path, names: list[str]) -> list[Link]:
    for name in names:
        if not gitops.ref_exists(root, f"refs/heads/{name}"):
            raise ChainError(f"branch {name} is in the chain but not in this clone — `git switch {name}` fetches it from {ORIGIN}")
    result = []
    for index, name in enumerate(names):
        parent = names[index - 1] if index else None
        pushed = gitops.ref_exists(root, f"refs/remotes/{ORIGIN}/{name}")
        ahead_o, behind_o = gitops.ahead_behind(root, f"{ORIGIN}/{name}", name) if pushed else (0, 0)
        ahead_p, behind_p = gitops.ahead_behind(root, parent, name) if parent else (0, 0)
        result.append(Link(name, parent, pushed, ahead_o, behind_o, ahead_p, behind_p))
    return result


def find_mrs(root: Path, tool: mrs.Tool, names: list[str]) -> dict[str, mrs.MergeRequest | None]:
    """The open merge request of every branch but the base."""
    return {name: mrs.open_for(root, tool, name) for name in names[1:]}


def wrong_targets(names: list[str], found: dict[str, mrs.MergeRequest | None]) -> dict[str, str]:
    """branch -> the branch its merge request should target instead, for the ones aimed elsewhere."""
    return {
        name: names[index - 1]
        for index, name in enumerate(names)
        if index and (mr := found.get(name)) is not None and mr.target != names[index - 1]
    }


def _count(ahead: int, behind: int) -> str:
    parts = ([f"ahead {ahead}"] if ahead else []) + ([f"behind {behind}"] if behind else [])
    return ", ".join(parts) or "in sync"


def render(
    chain: list[Link],
    found: dict[str, mrs.MergeRequest | None] | None,
    tool: mrs.Tool | None,
) -> str:
    """The table, base first. `found` is None when the merge requests could not be asked."""
    wrong = wrong_targets([link.branch for link in chain], found or {})
    rows = [("branch", "origin", "parent", "merge request")]
    for link in chain:
        origin = _count(link.ahead_of_origin, link.behind_origin) if link.pushed else "not pushed"
        if link.parent is None:
            parent, request = "-", "-"
        else:
            parent = f"merge up ({link.behind_parent} new)" if link.behind_parent else _count(link.ahead_of_parent, 0)
            mr = (found or {}).get(link.branch)
            if found is None or tool is None:
                request = "?"
            elif mr is None:
                request = "none open"
            else:
                request = f"{mr.label(tool)} -> {mr.target}  {mr.url}"
                if link.branch in wrong:
                    request += f"  ✗ should target {wrong[link.branch]}"
        rows.append((link.branch, origin, parent, request))
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=False)) + "  " + row[3] for row in rows)
