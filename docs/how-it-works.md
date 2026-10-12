# How suwgit for Windows works

The technical side of suwgit for Windows — the `master-windows` branch of
[suwalski-git](https://github.com/Manomenu/suwalski-git), whose `master` is the
Linux version. It runs under the Task Scheduler instead of systemd. The commit
logic is shared with `master` on purpose; only a thin platform layer differs. The technical side: how a commit gets made, what the model is asked
and how its answer is held in shape, the B2B commit format, how the report is
built, and where everything lives. For using it, see the [README](../README.md).

## Design rules

- **Standard library only.** The scheduled task runs a bare `pythonw.exe`; no
  venv has to stay healthy for it. `pytest` is a dev dependency and nothing
  else is.
- **Windowless.** Every subprocess gets `CREATE_NO_WINDOW`, so a sweep under
  `pythonw` never flashes a console, and every git output is decoded as UTF-8
  rather than the ANSI code page.
- **Plain `git` subprocesses**, no git library, so a machine with git is enough.
- **One code path for every commit.** `suwgit commit`, `suwgit push` and the
  scheduled sweep all go through `committer.commit_repo`, so they make exactly the same
  decisions about what is committable.
- **The git history is the database.** Nothing about past work is stored
  anywhere else; reports read it back from the commits.

## Modules

| module | what it does |
|---|---|
| `main.py` | the command line: parses arguments, resolves `--b2b` / `--project`, prints results |
| `committer.py` | the one operation: look at a dirty repository, ask the model, commit, push |
| `sweep.py` | one pass over the registry — what the scheduled task runs, every `interval_hours` |
| `llm.py` | the request to the vLLM server, the response schema, the prompts, parsing the answer |
| `gitops.py` | every git subprocess: status, diff, commit, push, log |
| `b2b.py` | the B2B commit format — writing it, and reading it back for a report |
| `report.py` | the PDF report, written by hand |
| `tenants.py` | the tenant index and the last `--b2b` used by hand |
| `config.py` | reading and writing `config.json`, the registry of repositories |
| `repolog.py` | the `.suwgit.log` blocker note inside a repository |
| `locking.py` | one suwgit process per repository at a time (`msvcrt` byte-range lock) |
| `initcmd.py`, `install.py`, `uninstall.py` | setup: launchers in `~/.local/bin`, the Task Scheduler entry |
| `logs.py`, `paths.py` | the capped log file, file locations |

## Making a commit

`commit_repo` runs, under a per-repository lock:

1. **Skip a repository in the middle of something.** A merge, rebase,
   cherry-pick, revert or bisect in progress is not suwgit's to commit.
2. **Read the working tree.** `git status --porcelain --untracked-files=all`,
   `git diff HEAD --stat` and `git diff HEAD`, plus the first 30 lines of up to
   40 new files (untracked files never show up in `git diff`). The diff is cut
   at `max_diff_chars`, but the status list is always sent in full, so the model
   still sees every file that changed.
3. **Wait while files are still being edited** (sweeps only). If any dirty file
   was modified within the last three quarters of the interval, the sweep leaves
   the repository for the next one. `suwgit commit` passes no wait: asking for a
   commit is the statement that you are done.
4. **Ask the model** for the message (below).
5. **Refuse on a suspected secret.** Nothing is committed or pushed; the reason
   goes to the log and to `.suwgit.log`.
6. **Commit** with `git add -A` and `git commit`, with the `--prefix` in front
   of the title if one was given. For a B2B commit, record the repository in
   the tenant index.
7. **Push** if pushing is on: the current branch, never forced, setting an
   upstream when there is none. A clean repository with unpushed commits is
   pushed too, so commits made by hand, and ones whose push failed earlier, go
   out.

Ordinary failures — an unreachable server, a rejected push, a git error — never
raise out of `commit_repo`, because a sweep calls it in a loop and one bad
repository must not stop the others.

## Asking the model

One POST to `{base_url}/chat/completions` with urllib, in the OpenAI
chat-completions dialect that vLLM speaks.

**Structured output.** The answer is constrained by a JSON schema
(`response_format`, which vLLM enforces with grammar-constrained decoding):

| field | meaning |
|---|---|
| `categories` | one or more of a fixed list — `feature bugfix refactor docs test chore style perf build config remove` |
| `description` | what was done, at most 15 words |
| `unsafe_for_commit` | true for a real secret in the added lines |
| `unsafe_reason` | what was found and where |
| `summary` | B2B only: one or two plain sentences for a non-technical client |
| `work_scope` | B2B only: 1–8 items, one piece of work each, in the same plain language |

With the schema in force, a chatty model cannot answer "Sure! Here is your
commit message: …", and the categories cannot drift, so
`git log --grep '\[bugfix'` keeps working months later. The title is assembled
by code as `[cat,cat] description`, with the description capped at 210
characters. A `--prefix` such as a ticket id is put in front by code too — the
model never sees it, so it cannot mangle it.

**Thinking is switched off** (`chat_template_kwargs: {enable_thinking: false}`).
Naming a commit is not a reasoning task: measured against a local Qwen3-8B, the
same diff took 952 completion tokens and ~18 s with thinking on, sometimes
running out of budget and returning nothing, against 36 tokens and 0.8 s with it
off, for the same answer.

**Fallbacks.** A server that rejects the schema (HTTP 400/404/422/501) is asked
again without it. Prose that comes back anyway is searched for the
`[categories] description` shape, with reasoning blocks and preambles stripped.
That fallback carries no secret-check answer, and in B2B mode there is no
fallback at all: without a summary and a work scope the commit is refused, since
it would be no record of work.

**The secret check** is part of the same answer. Real API keys, tokens,
passwords, connection strings with credentials, private keys and filled-in
`.env` files count; variable names like `api_key`, placeholders, example files,
public keys, obviously fake test values and keys being *removed* do not. It is a
language model's judgement — a safety net, not a guarantee.

## The B2B commit format

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

- The **title** stays technical, for the developer and `git log`.
- **Summary** and **Work-Scope** are for the client. The B2B prompt describes
  the reader as a business owner who knows the product, not its code, bans
  jargon, file names and identifiers, asks for concrete work rather than
  inflated benefits, and gives a good and a bad example.
- **Project**, **Tenant** and **Work-Period** are git trailers, so
  `git log --format='%(trailers)'` reads them too.
- **Work-Period** is an ISO 8601 interval with the UTC offset in it, so it
  means the same on any machine. It runs from the oldest modification time
  among the committed files to the newest plus two minutes, never past the
  moment of the commit; a commit of deletions only is dated at that moment. A
  file saved twice only remembers its last save, so the start is an estimate on
  the late side.

Writing and reading the format both live in `b2b.py`, so they cannot drift
apart. A commit counts as B2B when it has a parseable Work-Period and at least
one Work-Scope item; Summary is optional when reading, so a report can show
older commits by their title.

### Which tenant and project a commit gets

On `suwgit commit` / `push`, `--b2b` and `--project` resolve in this order:

1. a value given on the command line;
2. the repository's `default_tenant` / `default_project`, from `register`;
3. the tenant and project of the last `--b2b` commit made by hand in this
   repository (`last-b2b.json`) — and then suwgit asks before committing,
   Enter meaning yes, `-y` skipping the question, no terminal meaning no;
4. nothing: an error naming both ways out.

The last project is reused only for the same tenant, so a commit for one client
never inherits another client's project. Without `--project`, the project falls
back the same way and ends at the folder name.

A scheduled sweep commits B2B wherever a repository has a `default_tenant`,
under its defaults, and never asks.

## The report

`suwgit report --tenant <tenant>` has no database behind it:

1. `tenants.json` lists every repository that ever got a B2B commit for the
   tenant; each B2B commit adds its repository there.
2. Each of those repositories' history is read with
   `git log --all --author=<your user.email>`, so work on unmerged branches
   counts and colleagues' commits in a shared repository do not.
3. Commits are parsed back with `b2b.parse`, filtered by tenant and by the day
   their work started (both ends of the range included), and grouped by project.

The PDF is written by hand in `report.py` instead of with a library: the text
uses Helvetica in WinAnsi encoding, one of the fonts every PDF reader has built
in, so nothing is embedded and a report is a few kilobytes. Word wrapping
measures text with Helvetica's character widths. That encoding covers English
and Western European text; Polish letters need an embedded font, which is on the
[TODO](../TODO.md) list together with a report language switch.

`--format json` prints the same report to stdout instead of writing a PDF —
stdout then holds the JSON and nothing else, so it pipes into `jq`. It carries
what tooling needs on top of the PDF: the tenant, each commit's technical title,
full hash, repository path and exact Work-Period start and end with their UTC
offset. An empty range is an empty `projects` list, not an error.

```json
{
  "tenant": "lokum",
  "period": {"from": "2026-09-13", "to": "2026-10-12"},
  "prepared_by": "Piotr Gugnowski",
  "generated": "2026-10-12T19:30:00+02:00",
  "projects": [
    {
      "name": "Lokum Shop",
      "entries": [
        {
          "work_period": {"start": "2026-10-12T09:14+02:00", "end": "2026-10-12T11:04+02:00"},
          "summary": "Customers can now pay for their order directly in the shop.",
          "title": "[feature] added order checkout flow",
          "work_scope": ["added a checkout page where customers enter their address and pay"],
          "commit": "6e8e6cc565…",
          "repository": "/home/you/projects/shop",
          "author": "Maniumek"
        }
      ]
    }
  ]
}
```

The tenant name is never printed in the PDF — it is the user's working name for
the client — only in the default file name, `<tenant>--<today>.pdf`.

## When it cannot commit

When something really blocks a repository — the model is unreachable, a
suspected secret is in the changes, the push is rejected — suwgit writes
`.suwgit.log` into that repository:

```
# .suwgit.log — why suwgit did not commit this repository. Newest last, at most 10 entries.
2026-09-11 08:25:12  LLM unavailable, changes left uncommitted: cannot reach http://vllm:8000  (×6)
2026-09-11 14:10:03  refused to commit — possible secret in the changes: deploy/prod.env holds an API key
```

The last ten blockers are kept, and a repeating one is counted rather than
repeated. The first note adds `.suwgit.log` to the repository's `.gitignore`.
The file is deleted the moment there is nothing left to do, whether suwgit
committed or the user did. Waiting for files to settle is not a blocker and
writes nothing.

## What it deliberately does not do

- **Force-push or pull.** A rejected push stops there: no `--force`, no
  automatic pull or rebase, because a rebase conflict in an unattended sweep is
  how work gets lost. The commit stays local and goes out once the divergence is
  sorted.
- **Retry.** A failed sweep waits for the next interval rather than hammering a
  busy GPU.
- **Write to a terminal from a sweep.** Everything goes to the log.
- **Collide with itself.** A per-repository lock keeps a scheduled sweep and a
  manual `suwgit commit` from working on the same index at once.

## Configuration reference

`~/.config/suwgit/config.json`:

```json
{
  "llm": {
    "base_url": "http://localhost:8000/v1",
    "model": "qwen3-8b",
    "api_key": "",
    "timeout_seconds": 180
  },
  "interval_hours": 1.5,
  "scheduled": true,
  "push": false,
  "max_diff_chars": 400000,
  "report_author": "Piotr Gugnowski",
  "repos": [
    {"path": "/home/you/projects/shop", "auto_commit": true, "default_tenant": "lokum", "default_project": "Lokum Shop"}
  ]
}
```

| key | meaning |
|---|---|
| `base_url` | the server's address, used exactly as given — if it needs `/v1`, write it |
| `model` | the model name the server reports |
| `api_key` | stored apart from this file (see below); may stay empty for a local server |
| `timeout_seconds` | how long to wait for an answer |
| `interval_hours` | how often the task sweeps, rounded to whole minutes — re-run `./bin/suwgit init` after changing it |
| `scheduled` | whether the task runs at all; off keeps it registered but disabled |
| `push` | push after each commit, sweeps included |
| `max_diff_chars` | how much diff the model reads; the default sends the whole diff in almost every case |
| `report_author` | the "Prepared by" name on reports; empty means the commits' author names |
| `repos` | the registry, written by `register`. A bare path, as older versions wrote it, still loads |


## Where things live

| what | where |
|---|---|
| config | `~/.config/suwgit/config.json` (that is `%USERPROFILE%\.config\suwgit`) |
| API key | `~/.local/state/suwgit/api_key` — never in the config file |
| log | `~/.local/state/suwgit/suwgit.log`, hard-capped at 5 MB |
| locks | `~/.local/state/suwgit/locks/` |
| tenant index | `~/.local/state/suwgit/tenants.json` — kept by `uninstall`, removed by `--purge` |
| last `--b2b` | `~/.local/state/suwgit/last-b2b.json`, per repository |
| blocker notes | `.suwgit.log` in the repository itself |
| launchers | `~/.local/bin/suwgit` (Git Bash), `~/.local/bin/suwgit.cmd` (cmd, PowerShell) |
| schedule | Task Scheduler → Task Scheduler Library → `suwgit` |

Deliberately not `%APPDATA%`: the Microsoft Store build of Python redirects
writes there into its own package folder, so the config you open in an editor
would not be the one suwgit reads.

## The scheduled task

The task runs as you, only while you are logged in (no stored password), under
`pythonw.exe`, with every git call windowless — a sweep never flashes a console.
A sweep missed while the machine was asleep runs as soon as it wakes. Git hooks
in your repositories run as usual; a tool they need must be on the **Windows**
user PATH, not only in `~/.bashrc`, because a scheduled task never sees Git
Bash's PATH. The launchers and the task carry the path of the Python that ran
`./bin/suwgit init`, so re-run it after reinstalling Python.

## Why not WSL

It would work, but clumsily: git on `/mnt/c` goes through WSL's file bridge and
is slow on a big repository; WSL's git has its own config, credentials and line
ending settings beside the Windows ones; and a cron job inside WSL only runs
while the WSL VM happens to be up. Native Windows Python, Git for Windows and the
Task Scheduler need none of that.

## Tests

`scripts/.internal/check.sh` (or `just check`) runs ruff, the format check, the
lockfile check, pytest and shellcheck. Tests build real git repositories in a
temporary directory and point every suwgit path at a sandbox, and never touch
the real Task Scheduler.
