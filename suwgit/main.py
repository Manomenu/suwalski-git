"""suwgit's command line.

Thin dispatch: each subcommand validates its arguments, calls into a module,
and turns the result into terminal output. All the thinking lives elsewhere.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_module
from . import daemon, gitops, initcmd, install, logs, paths, uninstall
from .committer import commit_repo

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def _fail(message: str) -> int:
    print(f"{RED}✗{RESET} {message}", file=sys.stderr)
    return 1


def cmd_register(args: argparse.Namespace) -> int:
    try:
        root = gitops.find_repo_root(Path(args.folder))
    except gitops.GitError as exc:
        return _fail(str(exc))

    try:
        if not gitops.remotes(root):
            return _fail(f"{root} has no git remote — add one before registering it")
    except gitops.GitError as exc:
        return _fail(str(exc))

    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        return _fail(str(exc))

    if not config_module.register(config, root):
        print(f"{root} is already registered")
        return 0

    config_module.save(config)
    print(f"{GREEN}✓{RESET} registered {BOLD}{root}{RESET}  {DIM}(sweep every {config.interval_hours:g} h){RESET}")
    return 0


def cmd_unregister(args: argparse.Namespace) -> int:
    try:
        root = gitops.find_repo_root(Path(args.folder))
    except gitops.GitError as exc:
        return _fail(str(exc))

    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        return _fail(str(exc))

    if not config_module.unregister(config, root):
        return _fail(f"{root} is not registered")

    config_module.save(config)
    print(f"{GREEN}✓{RESET} unregistered {root}")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        return _fail(str(exc))

    print(f"{BOLD}config{RESET}   {config_module.source_file()}")
    print(f"{BOLD}model{RESET}    {config.llm.model or '—'} {DIM}@ {config.llm.base_url or '—'}{RESET}")
    print(f"{BOLD}sweep{RESET}    every {config.interval_hours:g} h   {DIM}service: {install.service_status()}{RESET}")
    print(f"{BOLD}push{RESET}     {'yes, after every commit' if config.push else 'no, commits stay local'}")
    print(f"{BOLD}log{RESET}      {paths.LOG_FILE}")
    print(f"\n{BOLD}registered{RESET}")
    if not config.repos:
        print(f"  {DIM}nothing yet — suwgit register <folder>{RESET}")
        return 0
    for entry in config.repos:
        root = Path(entry)
        if not (root / ".git").exists():
            state = f"{RED}missing{RESET}"
        else:
            try:
                state = "dirty" if gitops.read_working_tree(root, config.max_diff_chars).is_dirty else f"{DIM}clean{RESET}"
            except gitops.GitError:
                state = f"{RED}unreadable{RESET}"
        print(f"  {entry}  {state}")
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    try:
        root = gitops.find_repo_root(Path(args.path))
    except gitops.GitError as exc:
        return _fail(str(exc))

    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        return _fail(str(exc))

    push = True if getattr(args, "force_push", False) else args.push
    print(f"{DIM}asking {config.llm.model or 'the model'} about {root}…{RESET}")
    result = commit_repo(config, root, push=push)

    if result.unsafe:
        print(f"{RED}✗ refused to commit{RESET} {root}", file=sys.stderr)
        print(f"  {BOLD}{result.reason}{RESET}", file=sys.stderr)
        print(f"  {DIM}Nothing was committed. Check the changes, and commit by hand if this is a false alarm.{RESET}", file=sys.stderr)
        return 1

    if not result.committed and not result.pushed and not result.push_error:
        return _fail(f"{root}: {result.reason}")

    if result.committed:
        print(f"{GREEN}✓{RESET} {root}  {DIM}{result.commit}{RESET}")
        print(f"  {BOLD}{result.message}{RESET}")
    else:
        print(f"{GREEN}✓{RESET} {root}  {DIM}nothing to commit — {result.reason}{RESET}")
    if result.pushed:
        print(f"  {DIM}{result.pushed}{RESET}")
    if result.push_error:
        print(f"  {RED}✗{RESET} committed, but the push failed: {result.push_error}", file=sys.stderr)
        return 1
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    if args.once:
        daemon.run_once()
        return 0
    return daemon.run()


def cmd_uninstall(args: argparse.Namespace) -> int:
    try:
        return uninstall.run(purge=args.purge, assume_yes=args.yes)
    except (KeyboardInterrupt, EOFError):
        print("\naborted, nothing removed")
        return 1


def cmd_logs(args: argparse.Namespace) -> int:
    return logs.show(args.lines, args.follow)


def cmd_init(_args: argparse.Namespace) -> int:
    try:
        return initcmd.run()
    except (KeyboardInterrupt, EOFError):
        print("\naborted, nothing written")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="suwgit",
        description="Commits your repositories under names written by a local LLM.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="interactive setup: LLM, interval, autostart, PATH").set_defaults(func=cmd_init)

    register = sub.add_parser("register", help="watch a repository (it must have a git remote)")
    register.add_argument("folder")
    register.set_defaults(func=cmd_register)

    unregister = sub.add_parser("unregister", help="stop watching a repository")
    unregister.add_argument("folder")
    unregister.set_defaults(func=cmd_unregister)

    sub.add_parser("list", help="show the config and every registered repository").set_defaults(func=cmd_list)

    commit = sub.add_parser("commit", help="commit the closest repository above a path, right now")
    commit.add_argument("path", nargs="?", default=".")
    push_choice = commit.add_mutually_exclusive_group()
    push_choice.add_argument("--push", action="store_true", default=None, help="push afterwards, whatever the config says")
    push_choice.add_argument("--no-push", dest="push", action="store_false", help="commit only, whatever the config says")
    commit.set_defaults(func=cmd_commit)

    # The same thing with the push forced on, for when that is what you mean.
    push_cmd = sub.add_parser("push", help="commit and push the closest repository above a path")
    push_cmd.add_argument("path", nargs="?", default=".")
    push_cmd.set_defaults(func=cmd_commit, push=True, force_push=True)

    daemon_cmd = sub.add_parser("daemon", help="the background loop (systemd runs this)")
    daemon_cmd.add_argument("--once", action="store_true", help="one sweep, then exit")
    daemon_cmd.set_defaults(func=cmd_daemon)

    uninstall_cmd = sub.add_parser("uninstall", help="remove suwgit from PATH and systemd, and its logs")
    uninstall_cmd.add_argument("--purge", action="store_true", help="also delete the config and the API key")
    uninstall_cmd.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    uninstall_cmd.set_defaults(func=cmd_uninstall)

    logs_cmd = sub.add_parser("logs", help="show the daemon log")
    logs_cmd.add_argument("-n", "--lines", type=int, default=200)
    logs_cmd.add_argument("-f", "--follow", action="store_true")
    logs_cmd.set_defaults(func=cmd_logs)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
