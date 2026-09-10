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
vLLM server you run. Pushing is opt-in: leave it off and the commits sit in your
local history until you decide what to do with them.

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
git clone https://github.com/Manomenu/suwalski-git ~/repos/suwalski-git
cd ~/repos/suwalski-git
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
| `suwgit commit [path]` | commit the closest repository above `path` now, and print the message (`--push` / `--no-push` override the config for one run) |
| `suwgit push [path]` | the same thing with the push forced on |
| `suwgit logs [-n N] [-f]` | the daemon log, through [`bat`](https://github.com/sharkdp/bat) if you have it |
| `suwgit daemon [--once]` | the loop systemd runs |
| `suwgit uninstall [--purge]` | remove it from `PATH`, systemd and its logs; **keeps your config** unless `--purge` |

## Good messages, not chatter

The model is held to a fixed answer shape, so you get a commit message and
nothing else — no "Sure, here you go!", no stray formatting, no essay. It also
cannot invent its own labels: categories come from one fixed list, so
`git log --grep '\[bugfix'` keeps working months later.

```
feature   bugfix   refactor   docs   test   chore
style     perf     build      config remove
```

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
| `interval_hours` | how often to look; hours only |
| `push` | push after each commit, sweeps included; off by default |
| `max_diff_chars` | how much of the diff the model reads |

The default `max_diff_chars` sends the whole diff in almost every case. Lower it
if your model has a small context window, or if you would rather it read less
and answer faster. When a diff is too big it gets trimmed, but the list of
changed files is always sent, so the message still covers everything that moved.

If you keep your dotfiles in a [GNU Stow](https://www.gnu.org/software/stow/)
tree, `init` can put the config there instead of `~/.config`, and registering a
repository updates the tracked file directly.

## Where things live

| what | where |
|---|---|
| config | `~/.config/suwgit/config.json`, or a stow symlink into your dotfiles |
| API key | `~/.local/state/suwgit/api_key`, chmod 600 — **never** in the config file, which may be tracked by git |
| log | `~/.local/state/suwgit/suwgit.log`, one file, hard-capped at 5 MB |
| locks | `~/.local/state/suwgit/locks/` |
| unit | `~/.config/systemd/user/suwgit.service` |

## What it deliberately does not do

- **It does not push unless you say so.** `push` is `false` by default:
  committing for you is one thing, publishing on your behalf is another. With it
  on, the current branch is pushed after every commit — daemon sweeps included —
  and a branch with no upstream gets one, so a commit the daemon made on a new
  branch does not sit there invisibly. A sweep also pushes a repository that has
  **nothing to commit but something unpushed**, so commits you made by hand, and
  ones whose push failed earlier, still go out.
- **It never forces, and never pulls.** If the remote has moved on, the push is
  rejected and that is where it stops: no `--force`, no automatic pull or
  rebase, because a rebase conflict in an unattended daemon is how work gets
  lost. The commit is safe locally and goes out with the next sweep, once you
  have sorted the divergence yourself.
- **It stays quiet.** The daemon never writes to a terminal. An unreachable
  server is a `WARNING` in the log and nothing else; your changes are left
  uncommitted and picked up on the next sweep.
- **It does not retry.** A failed sweep is not worth hammering a busy GPU for —
  the changes will still be there in 1.5 hours.
- **It never commits into a mess.** A repository in the middle of a merge,
  rebase, cherry-pick or bisect is left alone until you have finished. The
  daemon and a manual `suwgit commit` can never collide over the same
  repository.

## License

[MIT](LICENSE)
