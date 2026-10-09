# suwgit for Windows

**Your working tree, committed for you, under a name a local LLM wrote.**

A Windows port of [suwalski-git](https://github.com/Manomenu/suwalski-git):
the same tool, run by the **Task Scheduler** instead of systemd, used from
**Git Bash**, cmd or PowerShell alike.

Register a repository and forget about it. Every 1.5 hours a scheduled sweep
looks at whatever you have left uncommitted, and — as long as you have stopped
touching it — asks your own vLLM server what happened, and commits it:

```
[feature] added revenue chart to main page dashboard
[refactor,bugfix] added builder schema for report creator and fixed main tab not opening
[feature,refactor,bugfix] added csv export for revenue chart and removed broken tabs module
```

No API keys to a cloud provider, no code leaving your network — it talks to a
vLLM server you run. Pushing is opt-in: leave it off and the commits sit in your
local history until you decide what to do with them.

Or skip the waiting and commit right now, from anywhere inside the repository —
with a ticket id in front, if you work against a tracker:

```console
$ suwgit push -p EH-3311111
✓ C:\Users\you\projects\dashboard  99f8156
  EH-3311111 [feature] added csv export for revenue chart
  pushed master
```

## Why

Uncommitted work is invisible work. It does not survive a bad rebase, it cannot
be bisected, and "wip" tells you nothing six weeks later. suwgit turns the pile
of changes you have not gotten around to committing into a readable history,
without asking you to stop what you are doing.

## Why not WSL

It would work, but clumsily: git on `/mnt/c` goes through WSL's file bridge and
is slow on a big repository; WSL's git has its own config, credentials and line
ending settings beside the Windows ones; and a cron job inside WSL only runs
while the WSL VM happens to be up. Native Windows Python, Git for Windows and the
Task Scheduler need none of that.

## Requirements

- Windows 10/11 with [Git for Windows](https://gitforwindows.org/) (Git Bash)
- Python 3.12+ — **no dependencies**, standard library only (the Microsoft Store
  build is fine)
- [`just`](https://github.com/casey/just) — every setup step is a recipe
- A [vLLM](https://github.com/vllm-project/vllm) server, or anything else that
  speaks the OpenAI `/chat/completions` dialect
- For development only: [`uv`](https://docs.astral.sh/uv/), and `shellcheck` for the full gate

## Install

```bash
git clone <this repo> ~/Repos/suwalski-git-win
cd ~/Repos/suwalski-git-win
just install
```

`just install` asks for your server's URL and model, how often to sweep, whether
the schedule is on and whether to push. It then writes two launchers into
`~/.local/bin` (`suwgit` for Git Bash, `suwgit.cmd` for cmd and PowerShell) and
registers a Task Scheduler entry named `suwgit`. Open a new terminal, then:

```bash
suwgit register ~/projects/dashboard
suwgit list
```

`just install` is re-runnable — every prompt offers your current setting as its
default, and the task is replaced, not duplicated. Re-run it after changing the
interval, or after reinstalling Python: the launchers and the task carry the
path of the interpreter that ran the install.

## just recipes

| recipe | what it does |
|---|---|
| `just install` | interactive setup: config, launchers on PATH, scheduled task |
| `just uninstall [--purge]` | remove the launchers, the task and the logs; **keeps your config** unless `--purge` |
| `just task` | everything Windows knows about the task: state, last result, next run |
| `just task-run` | start a sweep through the scheduler now — the real, windowless path |
| `just check` | the quality gate: ruff, pytest, shellcheck, lockfile |
| `just fmt` / `just sync` | ruff autofix and format / dev dependencies |

## Commands

| command | what it does |
|---|---|
| `suwgit register <folder>` | watch a repository (it must have a git remote) |
| `suwgit unregister <folder>` | stop watching it |
| `suwgit list` | config, task state, and which repositories are dirty |
| `suwgit commit [path] [-p PREFIX]` | commit the closest repository above `path` now, and print the message (`--push` / `--no-push` override the config for one run) |
| `suwgit push [path] [-p PREFIX]` | the same thing with the push forced on |
| `suwgit logs [-n N] [-f]` | the sweep log, through [`bat`](https://github.com/sharkdp/bat) if you have it |
| `suwgit sweep` | one pass over every registered repository — what the scheduled task runs |
| `suwgit init` / `suwgit uninstall` | what `just install` / `just uninstall` call |

`-p` / `--prefix` puts its argument in front of the model's message, as given:
`suwgit push -p EH-3311111` commits `EH-3311111 [feature] example`. The model
never sees the prefix, so it cannot mangle it. Scheduled sweeps add none.

## Good messages, not chatter

The model is held to a fixed answer shape, so you get a commit message and
nothing else — no "Sure, here you go!", no stray formatting, no essay. It also
cannot invent its own labels: categories come from one fixed list, so
`git log --grep '\[bugfix'` keeps working months later.

```
feature   bugfix   refactor   docs   test   chore
style     perf     build      config remove
```

## It stops before committing a secret

Every time it looks at your changes, the model is also asked whether they are
safe to commit at all. If it spots an API key, an access token, a password, a
private key or a filled-in `.env`, **nothing is committed and nothing is
pushed** — the changes stay in your working tree and the reason goes to the log:

```
WARNING  ~/projects/api: REFUSED to commit — possible secret in the changes
         (secrets/prod.env contains a real OpenAI API key in the added lines)
```

A secret in a git history is not undone by a later commit, and a sweep
commits while you are not watching, so this is the one moment anything can stop
it. Placeholders, `.env.example` files and variables merely *named* `api_key`
are left alone.

Treat it as a safety net, not a guarantee: it is a language model's judgement,
so it will not catch everything. Keep your `.gitignore` honest.

## Configuration

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
  "repos": []
}
```

| key | meaning |
|---|---|
| `base_url` | your server's address, written exactly as it works — suwgit uses it as given and adds nothing, so if it needs `/v1` on the end, put it there |
| `model` | the model name your server reports |
| `api_key` | can stay empty for a local server that does not check one |
| `interval_hours` | how often to look; hours only, rounded to whole minutes — re-run `just install` after changing it |
| `scheduled` | whether the task runs at all; off keeps it registered but disabled |
| `push` | push after each commit, sweeps included; off by default |
| `max_diff_chars` | how much of the diff the model reads |

The default `max_diff_chars` sends the whole diff in almost every case. Lower it
if your model has a small context window, or if you would rather it read less
and answer faster. When a diff is too big it gets trimmed, but the list of
changed files is always sent, so the message still covers everything that moved.

## Where things live

| what | where |
|---|---|
| config | `~/.config/suwgit/config.json` (that is `%USERPROFILE%\.config\suwgit`) |
| API key | `~/.local/state/suwgit/api_key` — **never** in the config file |
| log | `~/.local/state/suwgit/suwgit.log`, one file, hard-capped at 5 MB |
| locks | `~/.local/state/suwgit/locks/` |
| blocker notes | `.suwgit.log` in the repository itself — see below |
| launchers | `~/.local/bin/suwgit`, `~/.local/bin/suwgit.cmd` |
| schedule | Task Scheduler → Task Scheduler Library → `suwgit` |

Deliberately not `%APPDATA%`: the Microsoft Store build of Python redirects
writes there into its own package folder, so the config you open in an editor
would not be the one suwgit reads.

The task runs as you, only while you are logged in (no stored password), under
`pythonw.exe`, with every git call windowless — a sweep never flashes a console.
A sweep missed while the machine was asleep runs as soon as it wakes. Git hooks
in your repositories run as usual; a tool they need must be on the **Windows**
user PATH, not only in `~/.bashrc`, because a scheduled task never sees Git
Bash's PATH.

## When it cannot commit

A background job that fails silently is one you stop trusting. So when something
really blocks a repository — the model is unreachable, a suspected secret is in
the changes, the push is rejected — suwgit leaves a note **in that repository**,
as `.suwgit.log`:

```
# .suwgit.log — why suwgit did not commit this repository. Newest last, at most 10 entries.
2026-09-11 08:25:12  LLM unavailable, changes left uncommitted: cannot reach http://vllm:8000  (×6)
2026-09-11 14:10:03  refused to commit — possible secret in the changes: deploy/prod.env holds an API key
```

You notice a project has not been committed for days while you are standing in
it, not while reading a central log — so the answer is kept where the question
gets asked. The last ten blockers are kept; a blocker that keeps repeating is
counted rather than repeated, so one server outage cannot push out the other
nine things that went wrong. The first note also adds `.suwgit.log` to the
repository's `.gitignore`, creating that file if there is none — one ignored
line is a smaller price than a blocker nobody hears about.

**The file is deleted the moment there is nothing left to do** — whether suwgit
committed and pushed, or you did it by hand. So if it is there, something is
wrong right now; if it is not, nothing is.

Waiting is not blocking: nothing is written when suwgit is simply giving you
time to finish editing.

## What it deliberately does not do

- **It does not push unless you say so.** `push` is `false` by default:
  committing for you is one thing, publishing on your behalf is another. With it
  on, the current branch is pushed after every commit — scheduled sweeps included —
  and a branch with no upstream gets one, so a commit a sweep made on a new
  branch does not sit there invisibly. A sweep also pushes a repository that has
  **nothing to commit but something unpushed**, so commits you made by hand, and
  ones whose push failed earlier, still go out.
- **It never forces, and never pulls.** If the remote has moved on, the push is
  rejected and that is where it stops: no `--force`, no automatic pull or
  rebase, because a rebase conflict in an unattended sweep is how work gets
  lost. The commit is safe locally and goes out with the next sweep, once you
  have sorted the divergence yourself.
- **It stays quiet.** A scheduled sweep never writes to a terminal. An unreachable
  server is a `WARNING` in the log and nothing else; your changes are left
  uncommitted and picked up on the next sweep.
- **It does not retry.** A failed sweep is not worth hammering a busy GPU for —
  the changes will still be there in 1.5 hours.
- **It does not commit while you are still working.** A sweep leaves a
  repository alone if any uncommitted file was touched in the last three
  quarters of the interval — 67 minutes at the default 1.5 hours. Committing
  mid-edit gives you half a refactor under a message written about work that is
  not done yet. Nothing is lost by waiting: the next sweep sees the same changes
  plus whatever you added. `suwgit commit` by hand ignores this entirely —
  asking for a commit is the statement that you are finished.
- **It never commits into a mess.** A repository in the middle of a merge,
  rebase, cherry-pick or bisect is left alone until you have finished. A
  scheduled sweep and a manual `suwgit commit` can never collide over the same
  repository.

## License

[MIT](LICENSE)
