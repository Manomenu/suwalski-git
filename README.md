# suwgit

**Your working tree, committed for you, under a name a local LLM wrote.**

Register a repository and forget about it. Every 1.5 hours a background daemon
looks at whatever you have left uncommitted, asks your own vLLM server what
happened, and commits it:

```
[feature] added revenue chart to main page dashboard
[refactor,bugfix] added builder schema for report creator and fixed main tab not opening
[feature,refactor,bugfix] added csv export for revenue chart and removed broken tabs module
```

No API keys to a cloud provider, no code leaving your network — it talks to a
vLLM server you run. Nothing is pushed; the commits sit in your local history
until you decide what to do with them.

Or skip the waiting and commit right now, from anywhere inside the repository:

```console
$ suwgit commit .
✓ /home/you/projects/dashboard  99f8156
  [feature,refactor,bugfix] added csv export for revenue chart and removed broken tabs module
```

## Why

Uncommitted work is invisible work. It does not survive a bad rebase, it cannot
be bisected, and "wip" tells you nothing six weeks later. suwgit turns the pile
of changes you have not gotten around to committing into a readable history,
without asking you to stop what you are doing.

## Requirements

- Linux with systemd (the daemon runs as a **user** service)
- Python 3.12+ — **no dependencies**, standard library only
- git
- A [vLLM](https://github.com/vllm-project/vllm) server, or anything else that
  speaks the OpenAI `/chat/completions` dialect

## Install

```bash
git clone https://github.com/<you>/suwgit ~/repos/suwgit
cd ~/repos/suwgit
./bin/suwgit init
```

`init` asks where to keep the config, for your server's URL and model, how often
to sweep, and whether to start with your session. It then symlinks
`~/.local/bin/suwgit` and installs a systemd user unit. Open a new terminal so
the command is on your `PATH`, then:

```bash
suwgit register ~/projects/dashboard
suwgit list
```

`init` is re-runnable — every prompt offers your current setting as its default.

## Commands

| command | what it does |
|---|---|
| `suwgit init` | interactive setup; safe to run again |
| `suwgit register <folder>` | watch a repository (it must have a git remote) |
| `suwgit unregister <folder>` | stop watching it |
| `suwgit list` | config, service state, and which repositories are dirty |
| `suwgit commit [path]` | commit the closest repository above `path` now, and print the message |
| `suwgit logs [-n N] [-f]` | the daemon log, through [`bat`](https://github.com/sharkdp/bat) if you have it |
| `suwgit daemon [--once]` | the loop systemd runs |
| `suwgit uninstall [--purge]` | remove it from `PATH`, systemd and its logs; **keeps your config** unless `--purge` |

## How the commit message is kept trustworthy

The message is **not** scraped out of free text. The request carries a JSON
schema (`response_format`), which vLLM enforces with grammar-constrained
decoding: `categories` can only hold values from a fixed list, and
`description` is its own field. The model cannot answer *"Sure! Here is your
commit message: …"*, because the grammar does not allow it.

Categories come from a closed vocabulary — `feature bugfix refactor docs test
chore style perf build config remove` — so `git log --grep '\[bugfix'` keeps
working.

Thinking is switched off (`chat_template_kwargs.enable_thinking = false`).
Naming a commit is not a reasoning task, and measured on a local Qwen3-8B with
the same diff:

| | completion tokens | wall time | result |
|---|---|---|---|
| thinking on | 952 | ~18 s | correct — but sometimes **empty**, reasoning ate the whole budget |
| thinking off | 36 | 0.8 s | identical answer, every run |

If your backend cannot do guided decoding, suwgit asks again in prose and falls
back to parsing: `<think>` blocks, chatty preambles and code fences are all
stripped rather than committed.

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
  "open_on_system_start": true,
  "max_diff_chars": 400000,
  "repos": []
}
```

| key | meaning |
|---|---|
| `base_url` | used **exactly as written**, with `/chat/completions` appended — suwgit never adds `/v1` for you, so whatever `curl` reaches is what belongs here (worth checking if a reverse proxy rewrites paths) |
| `api_key` | may stay empty; vLLM ignores it, and suwgit sends `Bearer dummy` so a proxy that insists on the header is satisfied |
| `interval_hours` | how often the daemon sweeps; hours only |
| `max_diff_chars` | how much diff the model may see |

**Sizing `max_diff_chars`:** code tokenises at roughly 3.8 characters per token,
so a 200k-token context window holds about 740,000 characters of diff. The
default of 400,000 uses a bit over half of that. Context is rarely the binding
constraint — prefill time is, at roughly 75 s for 500,000 characters versus well
under a second at normal sizes. The whole diff is sent whenever it fits; if it
does not, the diff is clipped but `git status` is always sent in full, so the
model still sees every filename that changed.

`init` can keep the config in a [GNU Stow](https://www.gnu.org/software/stow/)
dotfiles tree instead of `~/.config` (set `DOTFILES` to point at it). suwgit
follows the stow symlink when it writes, so registering a repository updates the
tracked file directly without a re-stow.

## Where things live

| what | where |
|---|---|
| config | `~/.config/suwgit/config.json`, or a stow symlink into your dotfiles |
| API key | `~/.local/state/suwgit/api_key`, chmod 600 — **never** in the config file, which may be tracked by git |
| log | `~/.local/state/suwgit/suwgit.log`, one file, hard-capped at 5 MB |
| locks | `~/.local/state/suwgit/locks/` |
| unit | `~/.config/systemd/user/suwgit.service` |

## What it deliberately does not do

- **It does not push.** A remote is required at `register` time as a sanity
  check — an unsynced scratch directory should not be auto-committed — but the
  commits stay local. Pushing is yours to decide.
- **It stays quiet.** The daemon never writes to a terminal. An unreachable
  server is a `WARNING` in the log and nothing else; your changes are left
  uncommitted and picked up on the next sweep.
- **It does not retry.** A failed sweep is not worth hammering a busy GPU for —
  the changes will still be there in 1.5 hours.
- **It never commits into a mess.** A repository in the middle of a merge,
  rebase, cherry-pick or bisect is skipped. One repository is touched at a time
  (`flock`), so the daemon and a manual `suwgit commit` cannot race.

## Development

```bash
./scripts/test-solution.sh    # ruff + pytest
```

The tests run against real git repositories in `tmp_path` and a stub HTTP server
that speaks the vLLM dialect, so nothing needs a GPU. The parsing tests pin the
cases that actually happen with small models: chatty preambles, `<think>`
blocks, code fences, categories outside the enum, a server that rejects the
schema, and a proxy that ignores it.

`bin/suwgit` runs the package on the system `python3`. That is deliberate: a
daemon should not stop working because a virtualenv went stale.
