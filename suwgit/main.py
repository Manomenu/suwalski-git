"""suwgit's command line.

Thin dispatch: each subcommand validates its arguments, calls into a module,
and turns the result into terminal output. All the thinking lives elsewhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import b2b, daemon, gitops, initcmd, install, logs, paths, report, tenants, uninstall
from . import config as config_module
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

    status = config_module.register(
        config, root, auto_commit=args.auto_commit, default_tenant=args.default_tenant, default_project=args.default_project
    )
    entry = config.entry(root)
    if status == "unchanged":
        print(f"{root} is already registered  {DIM}({_describe(entry, config)}){RESET}")
        return 0

    config_module.save(config)
    print(
        f"{GREEN}✓{RESET} {'registered' if status == 'added' else 'updated'} {BOLD}{root}{RESET}  {DIM}({_describe(entry, config)}){RESET}"
    )
    return 0


def _describe(entry: config_module.RepoEntry, config: config_module.Config) -> str:
    """A registry entry's settings in a few words."""
    parts = [f"sweep every {config.interval_hours:g} h" if entry.auto_commit else "no auto-commit"]
    if entry.default_tenant:
        parts.append(f"--b2b means {entry.default_tenant}")
    if entry.default_project:
        parts.append(f"--project means {entry.default_project}")
    return ", ".join(parts)


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
    for entry in config.repos:
        root = Path(entry.path)
        if not (root / ".git").exists():
            state = f"{RED}missing{RESET}"
        else:
            try:
                state = "dirty" if gitops.read_working_tree(root, config.max_diff_chars).is_dirty else f"{DIM}clean{RESET}"
            except gitops.GitError:
                state = f"{RED}unreadable{RESET}"
        print(f"  {entry.path}  {state}  {DIM}{_describe(entry, config)}{RESET}")

    print(f"\n{BOLD}tenants{RESET}")
    rows = _tenant_rows(config)
    if not rows:
        print(f"  {DIM}none yet — suwgit commit --b2b <tenant>, or suwgit register <folder> --default-b2b <tenant>{RESET}")
    for tenant, root, project in rows:
        print(f"  {tenant}, {root.name}" + (f" {DIM}({project}){RESET}" if project else ""))
    return 0


def _tenant_rows(config: config_module.Config) -> list[tuple[str, Path, str]]:
    """(tenant, repository, project) for every repository a tenant is known in.

    Known means a B2B commit was made there (the tenant index), or it is the
    repository's default tenant. The project is the one a bare --b2b there would
    use for that tenant — the default, else the last used by hand — if there is one.
    """
    pairs = {(tenant, Path(path)) for tenant, repos in tenants.load().items() for path in repos}
    pairs |= {(e.default_tenant, Path(e.path)) for e in config.repos if e.default_tenant}

    rows = []
    for tenant, root in pairs:
        entry = config.entry(root)
        last = tenants.last_used(root)
        if entry and entry.default_tenant == tenant and entry.default_project:
            project = entry.default_project
        elif last and last[0] == tenant:
            project = last[1]
        else:
            project = ""
        rows.append((tenant, root, project))
    return sorted(rows, key=lambda row: (row[0].lower(), row[1].name.lower()))


# What a bare `--b2b` / `--project` parses to: "the repository's default".
FROM_DEFAULT = object()


def _b2b_for(entry: config_module.RepoEntry | None, root: Path, args: argparse.Namespace) -> tuple[str, str, bool]:
    """(tenant, project, from_last) for one commit; ("", "", False) for an ordinary one.

    A value given on the command line wins. A bare or missing one takes the
    default set with `register --default-b2b` / `--default-project`, and then
    what the last `--b2b` commit by hand in this repository used. `from_last`
    says that happened, and the caller asks before committing on it. A bare
    flag with nothing to take is an error that says how to give a value.
    """
    if args.tenant is None:
        if args.project is not None:
            raise ValueError(f"--project only goes with --b2b, e.g.  suwgit {args.command} --b2b lokum --project 'Lokum Shop'")
        return "", "", False

    last = tenants.last_used(root)
    from_last = False

    tenant = args.tenant
    if tenant is FROM_DEFAULT:
        if entry and entry.default_tenant:
            tenant = entry.default_tenant
        elif last:
            tenant, from_last = last[0], True
        else:
            raise ValueError(
                f"--b2b needs a tenant, and {root} has no default one. Either name it for this commit:\n"
                f"    suwgit {args.command} --b2b lokum\n"
                f"  or make it this repository's default, so a bare --b2b means it:\n"
                f"    suwgit register {root} --default-b2b lokum\n"
                f"  After one --b2b commit here, a bare --b2b offers the same tenant again"
            )

    project = args.project
    if project is FROM_DEFAULT or project is None:
        bare = project is FROM_DEFAULT
        # The last project belongs to the last tenant: never carry it over to another client.
        if entry and entry.default_project:
            project = entry.default_project
        elif last and last[0] == tenant and last[1]:
            project, from_last = last[1], True
        elif bare:
            raise ValueError(
                f"--project needs a name, and {root} has no default one. Either name it for this commit:\n"
                f"    suwgit {args.command} --b2b {tenant} --project 'Lokum Shop'\n"
                f"  or make it this repository's default:\n"
                f"    suwgit register {root} --default-project 'Lokum Shop'\n"
                f"  Leave --project out and the project is the folder name, {root.name}"
            )
        else:
            project = ""
    return tenant, project, from_last


def _confirm(question: str) -> bool:
    """A yes/no question where Enter means yes. No answer at all (no terminal) means no."""
    try:
        answer = input(f"{question} [Y/n] ").strip().lower()
    except EOFError:
        print()
        return False
    return answer in ("", "y", "yes", "t", "tak")


def cmd_commit(args: argparse.Namespace) -> int:
    try:
        root = gitops.find_repo_root(Path(args.path))
    except gitops.GitError as exc:
        return _fail(str(exc))

    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        return _fail(str(exc))

    try:
        tenant, project, from_last = _b2b_for(config.entry(root), root, args)
    except ValueError as exc:
        return _fail(str(exc))

    push = True if getattr(args, "force_push", False) else args.push
    if from_last and not args.yes:
        action = "Commit and push" if (config.push if push is None else push) else "Commit"
        question = f"{action} {root.name} as B2B for {BOLD}{tenant}{RESET}, project {BOLD}{project or root.name}{RESET} (last used here)?"
        if not _confirm(question):
            return _fail(
                f"nothing committed — name it instead:  suwgit {args.command} --b2b <tenant> --project <name>   (-y skips this question)"
            )
    print(f"{DIM}asking {config.llm.model or 'the model'} about {root}…{RESET}")
    result = commit_repo(config, root, push=push, tenant=tenant, project=project)
    if tenant and result.committed:
        try:
            tenants.remember(root, tenant, project)
        except OSError:
            pass  # only a convenience for the next bare --b2b

    if result.unsafe:
        print(f"{RED}✗ refused to commit{RESET} {root}", file=sys.stderr)
        print(f"  {BOLD}{result.reason}{RESET}", file=sys.stderr)
        print(f"  {DIM}Nothing was committed. Check the changes, and commit by hand if this is a false alarm.{RESET}", file=sys.stderr)
        return 1

    if not result.committed and not result.pushed and not result.push_error:
        return _fail(f"{root}: {result.reason}")

    if result.committed:
        title, _, body = result.message.partition("\n")
        print(f"{GREEN}✓{RESET} {root}  {DIM}{result.commit}{RESET}")
        print(f"  {BOLD}{title}{RESET}")
        for line in body.strip().splitlines():
            print(f"  {DIM}{line}{RESET}" if line else "")
    else:
        print(f"{GREEN}✓{RESET} {root}  {DIM}nothing to commit — {result.reason}{RESET}")
    if result.pushed:
        print(f"  {DIM}{result.pushed}{RESET}")
    if result.push_error:
        print(f"  {RED}✗{RESET} committed, but the push failed: {result.push_error}", file=sys.stderr)
        return 1
    return 0


REPORT_DAYS = 30


def _day(text: str) -> date:
    """A report date. ISO only: 9/10/2026 is September in one country and October in the next."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date — write it as YYYY-MM-DD, e.g. 2026-09-13") from None


def _report_author() -> str:
    """The configured Prepared by name; empty without a config, so the commits' authors stand in."""
    try:
        return config_module.load().report_author
    except config_module.ConfigError:
        return ""


def cmd_report(args: argparse.Namespace) -> int:
    today = date.today()
    last = args.to or today
    # Both ends count, so 30 days end to end is --to and the 29 before it.
    first = args.since or last - timedelta(days=REPORT_DAYS - 1)
    if first > last:
        return _fail(f"--from {first} is after --to {last}")

    repos = tenants.repos_of(args.tenant)
    if not repos:
        return _fail(f"no repository has ever had a B2B commit for {args.tenant} (index: {paths.TENANTS_FILE})")

    entries = b2b.collect(repos, args.tenant, first, last)
    if args.format == "json":
        # stdout carries the JSON and nothing else, so it pipes into jq; an empty
        # range is still a valid answer there, not an error.
        if args.output:
            return _fail("-o is for the PDF — send the JSON to a file with  > report.json")
        document = report.as_json(entries, args.tenant, first, last, datetime.now().astimezone(), author=_report_author())
        print(json.dumps(document, indent=2, ensure_ascii=False))
        return 0
    if not entries:
        return _fail(f"no B2B commits of yours for {args.tenant} between {first} and {last}, in {len(repos)} repositories")

    name = report.default_filename(args.tenant, today)
    target = Path(args.output or name).expanduser()
    # A folder — existing, or written with a trailing slash — gets the default name inside it.
    if args.output and (target.is_dir() or args.output.endswith("/")):
        target = target / name
    target = target.resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(report.build(entries, first, last, datetime.now().astimezone(), with_time=args.time, author=_report_author()))
    except OSError as exc:
        return _fail(f"cannot write {target}: {exc}")
    projects = sorted({e.project for e in entries})
    print(f"{GREEN}✓{RESET} {BOLD}{target}{RESET}")
    print(f"  {DIM}{len(entries)} commit(s) for {args.tenant}, {first} – {last}: {', '.join(projects)}{RESET}")
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


def _tenant(text: str) -> str:
    try:
        return tenants.check(text)
    except tenants.TenantError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _b2b_options(parser: argparse.ArgumentParser) -> None:
    """--b2b [TENANT] / --project [NAME] for one commit; bare, they take the registered default."""
    parser.add_argument(
        "--b2b",
        dest="tenant",
        metavar="TENANT",
        nargs="?",
        const=FROM_DEFAULT,
        type=_tenant,
        help="make this a B2B commit for this client; bare, the repository's --default-b2b",
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        nargs="?",
        const=FROM_DEFAULT,
        help="the Project field of this B2B commit; bare or left out, the repository's --default-project (else the folder name)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="do not ask before reusing the tenant and project of the last --b2b commit here",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="suwgit",
        description="Commits your repositories under names written by a local LLM.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="interactive setup: LLM, interval, autostart, PATH").set_defaults(func=cmd_init)

    register = sub.add_parser("register", help="watch a repository (it must have a git remote); again to change its settings")
    register.add_argument("folder")
    register.add_argument(
        "--auto-commit",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="let the daemon commit it on every sweep (default: on)",
    )
    defaults = register.add_mutually_exclusive_group()
    defaults.add_argument(
        "--default-b2b",
        dest="default_tenant",
        metavar="TENANT",
        type=_tenant,
        help="what a bare --b2b on commit/push means here; with auto-commit on, the daemon's commits are B2B for it too",
    )
    defaults.add_argument("--no-default-b2b", dest="default_tenant", action="store_const", const="", help="forget the default tenant")
    register.add_argument(
        "--default-project",
        metavar="NAME",
        help="what --project means here when bare or left out (otherwise the folder name); '' forgets it",
    )
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
    _b2b_options(commit)
    commit.set_defaults(func=cmd_commit)

    # The same thing with the push forced on, for when that is what you mean.
    push_cmd = sub.add_parser("push", help="commit and push the closest repository above a path")
    push_cmd.add_argument("path", nargs="?", default=".")
    _b2b_options(push_cmd)
    push_cmd.set_defaults(func=cmd_commit, push=True, force_push=True)

    report_cmd = sub.add_parser("report", help="a PDF of your B2B commits for one tenant in a date range")
    report_cmd.add_argument("--tenant", required=True, type=_tenant, help="the client, as given to --b2b")
    report_cmd.add_argument(
        "--from", dest="since", type=_day, help=f"first day, YYYY-MM-DD (default: the last {REPORT_DAYS} days up to --to)"
    )
    report_cmd.add_argument("--time", action="store_true", help="show the hours of every work period, not only the days")
    report_cmd.add_argument(
        "--format",
        choices=("pdf", "json"),
        default="pdf",
        help="pdf writes a file (default); json prints the report to stdout, for jq",
    )
    report_cmd.add_argument("--to", type=_day, help="last day, inclusive, YYYY-MM-DD (default: today)")
    report_cmd.add_argument(
        "-o",
        "--output",
        help="the PDF file, or a folder to put it in (default: <tenant>--<today>.pdf in the current folder)",
    )
    report_cmd.set_defaults(func=cmd_report)

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
