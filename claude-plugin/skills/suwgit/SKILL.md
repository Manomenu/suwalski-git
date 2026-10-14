---
name: suwgit
description: Set up and drive suwgit, the CLI that auto-commits git repositories with LLM-written messages, records B2B client work in commits, and builds client work reports. Use when the user mentions suwgit, wants their repositories committed or pushed with generated messages, wants to record work for a client (tenant) or add a ticket id (YouTrack, Jira) to a commit, asks for a work report for a client or a date range, or asks to install, configure or troubleshoot suwgit.
---

# suwgit

suwgit is a command-line tool, run through Bash. It commits a repository's
uncommitted changes under a message written by the user's own LLM server, can
mark a commit as work for a client (B2B), and builds PDF or JSON work reports
from those commits. Source and docs: https://github.com/Manomenu/suwalski-git
(`master` is Linux, `master-windows` is Windows).

## First: is it installed?

```bash
command -v suwgit && suwgit list
```

- `suwgit list` prints the config, every registered repository and every
  tenant as `tenant, folder (project)`. Read it before acting: it tells you the
  tenants and projects that exist, so you never guess one.
- `command not found`: suwgit is not installed. Go to **Setup**.
- `no config at …`: installed but never initialised. Go to **Setup**, step 2.

## Setup

`init` is interactive. It asks for the LLM server URL, model, API key, how
often to sweep, whether to push, and the name for reports. **You cannot answer
its questions through Bash**, so never run it yourself. Give the user the lines
to run, prefixed with `!` so they run in this session's terminal, and explain
the questions first.

Linux (needs systemd, Python 3.12+, git):

```
! git clone https://github.com/Manomenu/suwalski-git ~/repos/suwalski-git
! ~/repos/suwalski-git/bin/suwgit init
```

Windows (Git Bash, Python 3.12+, Git for Windows):

```
! git clone -b master-windows https://github.com/Manomenu/suwalski-git ~/Repos/suwalski-git
! ~/Repos/suwalski-git/bin/suwgit init
```

The questions, so the user can prepare answers:

- **Base URL** of an OpenAI-compatible server (vLLM), written as it works,
  e.g. `http://localhost:8000/v1`.
- **Model name** the server reports.
- **API key**: blank for a local server that does not check one.
- **Sweep interval** in hours (default 1.5), whether the background service
  starts with the system (Linux) or the scheduled task is on (Windows).
- **Push** after each commit: off is the safe default.
- **Name on reports** ("Prepared by"): the user's real name, as the client
  should read it.
- **E-mail** (optional): a mailbox to send reports from — its address, SMTP
  server, encryption (starttls 587 / ssl 465), login and password. Gmail and
  Outlook need an app password, not the account password. More mailboxes are
  added later with `! suwgit add-mail <address>` (the user runs it: it asks for
  the password); `suwgit remove-mail <address>` forgets one, and you may run
  that when asked.

Re-running `init` is safe: every question offers the current value, and Enter
keeps it. After `init`, a new terminal puts `suwgit` on PATH. Then register
repositories, which you may run yourself:

```bash
suwgit register ~/projects/shop                         # auto-commit, ordinary commits
suwgit register ~/projects/shop --default-b2b lokum --default-project "Lokum Shop"
suwgit register ~/projects/shared --no-auto-commit      # never committed by the service
```

A repository must have a git remote to be registered.

## Commands

Always write the path, then a quoted instruction if any, **before** the flags: a bare `--b2b` would otherwise take
the path for a tenant name.

| want | command |
|---|---|
| commit now | `suwgit commit [path]` |
| commit and push | `suwgit push [path]` |
| …as client work | add `--b2b <tenant> [--project "<name>"]` |
| …with a ticket id first in the title | add `-p <ID>`, e.g. `-p EH-3311111`; a bare `-p`, last, takes it from the branch name |
| …with how-to-test and how-to-review checklists for the merge request | add `-i` |
| …with the user's own wishes for the message | `suwgit commit [path] "<hint>"`, quoted, right after the path |
| …without pushing even if the config pushes | `suwgit commit [path] --no-push` |
| report, last 30 days, PDF in the current folder | `suwgit report --tenant <tenant>` |
| report for a range | add `--from YYYY-MM-DD --to YYYY-MM-DD` (both included) |
| report with hours | add `--time` |
| report saved elsewhere | add `-o <file or folder>` |
| report as data | `suwgit report --tenant <tenant> --format json` (stdout only; pipe to `jq`) |
| report without the AI project summaries | add `--no-summary` |
| report sent by e-mail | add `--mail <address> -y` — only after the user saw the PDF and agreed (see Rules); with several mailboxes also `--mail-from <address>` |
| setup, repositories, tenants | `suwgit list` |
| what the background service did | `suwgit logs -n 50` |
| stop watching a repository | `suwgit unregister <path>` |

Dates are ISO only. Turn "September", "last week" or "Q3" into exact
`YYYY-MM-DD` dates yourself, using today's date.

## B2B commits: never leave a question to the terminal

A B2B commit needs a tenant (the client's working name) and a project (the name
the client reads; the folder name if none). A bare `--b2b` can fall back to the
repository's default or to the last values used there, and then suwgit asks
`[Y/n]` in the terminal. **Your Bash has no terminal to answer it, so that
question always ends as "no" and nothing is committed.** So:

- Look up the tenant and project in `suwgit list` (`tenant, folder (project)`)
  and pass both explicitly: `--b2b lokum --project "Lokum Shop"`.
- Add `-y` only when the user has confirmed those exact values in this
  conversation, or named them themselves.
- If the user asks for client work but names no tenant, and `suwgit list`
  shows exactly one tenant for this repository, use it and say which. If
  there are several, or none, ask which client.
- Never invent a tenant. A new one is fine when the user names it.

## Reading reports

To answer a question about work done ("what did I do for lokum in
September?"), use JSON instead of making a PDF:

```bash
suwgit report --tenant lokum --from 2026-09-01 --to 2026-09-30 --format json --no-summary | jq '.projects[] | {name, entries: [.entries[] | {day: .work_period.start[:10], summary}]}'
```

In the JSON, an entry with `"reverted": "<day>"` is work a later revert
undid: it is listed, not delivered. Say so when you sum up work; `--hide-reverted`
leaves it out. One piece of work counts once even if it was cherry-picked or
rebased.

To undo client work, prefer `git revert <hash>`, or undo the change and run
`suwgit commit`: an exact undo is committed as a revert, never as new B2B work,
and the report marks the original as reverted.

Make the PDF only when the user wants the file, and tell them its path. A
report needs the LLM server for its project summaries; when it is down the
report is still made, without them.

## Rules

- **Commits and pushes are the user's call.** Run `commit` or `push` only when
  asked. A push publishes the branch.
- **A refused commit stays refused.** If suwgit reports a possible secret, show
  the reason and stop. Never work around it, and never commit those files by
  other means unless the user tells you to after seeing the reason.
- **Never force-push or pull to make a push go through.** A rejected push means
  the remote has commits this clone lacks. Say so, and let the user decide.
  The commit itself is already safe locally.
- **`init` is the user's to run** (see Setup). So is `uninstall`.
- **E-mail goes out only when asked, and only after a look.** `--mail` takes
  the recipient; the sender is always the user's mailbox from `init`. Make the
  report without `--mail` first, give the user the path, and add
  `--mail <address> -y` only after they confirm in this conversation, with
  exactly the addresses they gave. Without `-y` suwgit asks `[y/N]`, which your
  shell cannot answer, so nothing would be sent.

## Updating

`/suwgit:update` updates both suwgit (a fast-forward pull of a clean clone, and
a service restart on Linux) and this plugin (a copy Claude Code keeps, which
does not change by itself). After a plugin update the user runs
`/reload-plugins`.

## When something is wrong

- `.suwgit.log` in a repository explains why suwgit last could not commit it:
  model unreachable, suspected secret, rejected push. It disappears when the
  problem is gone.
- `suwgit logs -n 50` shows what the background service did.
- `LLM unavailable`: the server in `suwgit list` is down or unreachable.
  Nothing was committed, and the changes are untouched.
- `B2B mode needs a summary and a work scope`: the model's answer was
  incomplete. Retrying usually works.
