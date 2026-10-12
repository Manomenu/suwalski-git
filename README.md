# suwgit for Windows

**Your work, committed for you, and a report for your client.**

A Windows port of [suwalski-git](https://github.com/Manomenu/suwalski-git): the
same tool, run by the **Task Scheduler** instead of systemd, used from
**Git Bash**, cmd or PowerShell alike.

suwgit watches your git repositories and commits what you leave uncommitted,
under a clear message written by an AI model running on **your own server**.
No code leaves your network.

Working B2B? The same commits become a record of the work you did for a client,
in plain language a non-technical client understands, and one command turns
them into a PDF report.

```
[feature] added revenue chart to main page dashboard
[refactor,bugfix] added builder schema for report creator and fixed main tab not opening
```

## What you get

- **Nothing left uncommitted.** Register a repository and forget it. Every
  interval a scheduled sweep commits whatever you have stopped working on. It
  waits while you are still editing.
- **Messages worth reading.** Each commit says what changed, labelled `feature`,
  `bugfix`, `refactor` and so on, so you can find anything later.
- **Ticket ids where your team needs them.** Put a YouTrack or Jira task id in
  front of the commit title with one flag.
- **Proof of work for clients.** Mark a commit as client work and it records
  what was done, for which project and when, written for the client rather
  than for developers.
- **A PDF report in one command.** Every piece of work for a client over any
  date range, grouped by project. No spreadsheet, no time tracker, no database.
- **Safe by default.** It refuses to commit anything that looks like a password
  or an API key. It never force-pushes and never pulls on your behalf. Pushing
  is off until you turn it on.

## The report

```bash
suwgit report --tenant lokum
```

![The first page of an example work report](docs/report-example.png)

Each entry shows when the work was done, a short summary, the work behind it,
and the commit that proves it. By default the report covers the last 30 days
and shows days only:

| option | what it does |
|---|---|
| `--from 2026-09-13 --to 2026-10-13` | any range, both days included; dates as `YYYY-MM-DD` |
| `--time` | add the hours of every piece of work |
| `-o ~/invoices/` | where to save it: a file or a folder (default: the current folder, as `lokum--<today>.pdf`) |
| `--format json` | print the report as JSON instead, for scripts: `suwgit report --tenant lokum --format json \| jq` |

The report lists only your own commits, from every repository where you did
work for that client, on every branch.

## Recording work for a client

A client gets a **tenant**: a short working name only you see, such as `lokum`.
Commit with it:

```bash
suwgit commit --b2b lokum --project "Lokum Shop"
```

```
[feature] added order checkout flow

Summary: Customers can now pay for their order directly in the shop, and orders with a wrong total are stopped before payment.

Work-Scope:
- added a checkout page where customers enter their address and pay
- made the shop double-check the order total before taking payment

Project: Lokum Shop
Tenant: lokum
Work-Period: 2026-10-12T09:14+02:00/2026-10-12T11:04+02:00
```

The title stays technical, for you. The **Summary** and **Work-Scope** are for
the client: what changed for them, in plain words. **Project** is the name the
client sees, and the folder name if you give none. The tenant never appears in
the report.

### Less typing

Tell a repository who it works for, once:

```bash
suwgit register ~/projects/shop --default-b2b lokum --default-project "Lokum Shop"
```

| in that repository | commits as |
|---|---|
| `suwgit commit` | an ordinary commit |
| `suwgit commit --b2b` | client work for `lokum`, project Lokum Shop |
| `suwgit push --b2b acme` | client work for `acme` instead |
| a scheduled sweep | client work for `lokum`, every time |

Without a default, a bare `--b2b` offers what you used last time in that
repository and asks first. Enter confirms, and `-y` skips the question:

```console
$ suwgit push --b2b
Commit and push shop as B2B for lokum, project Lokum Shop (last used here)? [Y/n]
```

If a repository mixes client work with your own, register it with
`--no-auto-commit` and commit by hand. Then only what you mark with `--b2b`
counts as the client's.

## Ticket ids in commit titles

If your team requires every commit to name its task from YouTrack, Jira or
another tracker, add it with `-p` / `--prefix`:

```console
$ suwgit push -p EH-3311111
✓ C:\Users\you\projects\dashboard  99f8156
  EH-3311111 [feature] added csv export for revenue chart
  pushed master
```

The id goes in front of the title exactly as you typed it. The AI model never
sees it, so it cannot get it wrong. The tracker links the commit to the task as
usual. It works together with `--b2b`, and scheduled sweeps add no prefix.

## Install

You need Windows 10/11 with [Git for Windows](https://gitforwindows.org/),
Python 3.12+ (the Microsoft Store build is fine), and a
[vLLM](https://github.com/vllm-project/vllm) server or anything else that
speaks the OpenAI chat-completions API. There is nothing else to install.

In Git Bash:

```bash
git clone <this repo> ~/Repos/suwalski-git-win
cd ~/Repos/suwalski-git-win
./bin/suwgit init
```

`init` asks for your server, how often to commit, whether to push, and
your name for reports ("Prepared by"). It then puts `suwgit` on your PATH for
Git Bash, cmd and PowerShell, and adds a Task Scheduler entry named `suwgit`.
Run it again any time: every question offers your current answer, so Enter
keeps it. Open a new terminal, then:

```bash
suwgit register ~/projects/dashboard
suwgit list
```

## Commands

| command | what it does |
|---|---|
| `suwgit register <folder>` | watch a repository; run again to change its settings: `--default-b2b <tenant>`, `--default-project <name>`, `--no-auto-commit` |
| `suwgit unregister <folder>` | stop watching it |
| `suwgit list` | the setup, every repository and whether it has changes, and every client as `tenant, folder (project)` |
| `suwgit commit [path]` | commit now; `-p <ticket>` for a task id, `--b2b [tenant]` / `--project [name]` for client work, `-y` to skip the question, `--push` / `--no-push` |
| `suwgit push [path]` | commit now and push |
| `suwgit report --tenant <tenant>` | the PDF report; see [above](#the-report) |
| `suwgit logs [-n N] [-f]` | what the scheduled sweeps did |
| `suwgit sweep` | one pass over every repository, as the scheduled task does |
| `suwgit init` | set up, or change the setup; safe to run again |
| `suwgit uninstall [--purge]` | remove suwgit; your config stays unless `--purge` |

The scheduled task, from Git Bash:

```bash
MSYS_NO_PATHCONV=1 schtasks /Query /TN suwgit /V /FO LIST   # its state, last result, next run
MSYS_NO_PATHCONV=1 schtasks /Run /TN suwgit                 # run a sweep through it now
```

### With `just`

If you have [`just`](https://github.com/casey/just), the same steps are
recipes in the clone:

| recipe | runs |
|---|---|
| `just install` | `./bin/suwgit init` |
| `just uninstall [--purge]` | `./bin/suwgit uninstall` |
| `just task` / `just task-run` | the two `schtasks` lines above |
| `just check` | `scripts/.internal/check.sh`, the quality gate for development |

## When something goes wrong

If suwgit cannot commit a repository, because the AI server is down, a secret
was spotted or the push was rejected, it leaves a `.suwgit.log` note in that
repository saying why. The note disappears as soon as the problem is gone. A
secret is never committed: the changes stay in your working tree for you to
check.

If a git hook in your repository needs a tool, that tool must be on the
**Windows** user PATH, not only in `~/.bashrc`: a scheduled task never sees
Git Bash's PATH.

## More

- [How it works](docs/how-it-works.md): the commit flow, the AI model's
  instructions, the B2B commit format, how the report is built, the scheduled
  task, why not WSL, configuration reference and file locations.
- [TODO](TODO.md): what is planned next.

## License

[MIT](LICENSE)
