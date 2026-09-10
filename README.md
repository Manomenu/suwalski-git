# suwalski-git (`suwgit`)

A Fedora user daemon that commits for you. Register a repository, and every
1.5 h (configurable) suwgit looks at the uncommitted changes, asks a local vLLM
server to name them, and commits under that name:

```
[refactor,bugfix] added builder schema for raport creator and fixed main tab not opening
[feature] added revenue chart to main page dashboard
```

Categories come from a closed list — `feature bugfix refactor docs test chore
style perf build config remove` — so the history stays greppable.

## How the answer is kept trustworthy

The message is **not** scraped out of free text. The request carries a JSON
schema (`response_format`), which vLLM enforces with grammar-constrained
decoding: `categories` can only be values from the enum, `description` is its
own field. A model cannot answer "Yeah, sure! Here is your commit message: …"
because the grammar does not allow it.

Thinking is switched off (`chat_template_kwargs.enable_thinking = false`).
Naming a commit is not a reasoning task, and measured against qwen38 on the same
diff:

| | completion tokens | wall time | result |
|---|---|---|---|
| thinking on | 952 | ~18 s | correct, but sometimes **empty** — reasoning ate the whole budget |
| thinking off | 36 | 0.8 s | byte-identical answer, every run |

`parse_free_text` is the safety net for a backend that cannot do guided
decoding: it strips `<think>` blocks, preambles and code fences and finds the
message inside whatever came back.

## Install

```bash
git clone <remote> ~/repos/suwalski-git
cd ~/repos/suwalski-git
./bin/suwgit init          # interactive: config location, vLLM, interval, autostart
```

`init` writes the config, symlinks `~/.local/bin/suwgit`, and installs
`~/.config/systemd/user/suwgit.service`. Open a new terminal afterwards, then:

```bash
suwgit register ~/repos/some-project
```

## Commands

| command                       | what it does |
|-------------------------------|--------------|
| `suwgit init`                 | interactive setup, re-runnable |
| `suwgit register <folder>`    | watch a repository (it must have a git remote) |
| `suwgit unregister <folder>`  | stop watching it |
| `suwgit list`                 | config, service state, registered repositories and whether they are dirty |
| `suwgit commit [path]`        | commit the closest repository above `path` now, and print the message |
| `suwgit logs [-n N] [-f]`     | the daemon log, through `bat` when it is installed |
| `suwgit daemon [--once]`      | the loop systemd runs |
| `suwgit uninstall [--purge]`  | remove it from PATH, systemd and its logs; **keeps the config** unless `--purge` |

## Uninstalling

```bash
suwgit uninstall            # PATH symlink, systemd unit, logs and locks
suwgit uninstall --purge    # the above, plus the config and the API key
```

It lists every path first and the confirmation defaults to **no**. The config
survives by default, so reinstalling does not cost you the vLLM settings or the
list of registered repositories. The clone and every commit suwgit has already
made are never touched, and a real file at `~/.local/bin/suwgit` (as opposed to
our own symlink) is left alone.

## Talking to vLLM

`base_url` is used **exactly as written**, with `/chat/completions` appended —
suwgit never adds `/v1` for you, so whatever `curl` reaches is what to put in
the config. Behind the Caddy proxy on `.145` the prefix already maps onto the
vLLM root, so the config carries no `/v1`:

```json
"base_url": "http://100.92.219.27/vllm-local-145",
"model": "qwen38"
```

Code tokenises at **3.78 chars/token** on qwen38 (measured), so its 200,100-token
window holds roughly 740,000 characters of diff. `max_diff_chars` defaults to
400,000 (~106k tokens, ~53 % of the window); prefill, not context, is the cost —
about 75 s at 500k characters, versus under a second at normal sizes. Lower it
for a backend with a smaller window; the whole diff is sent whenever it fits.

If that rewrite is ever removed from Caddy, the URL becomes
`http://100.92.219.27/vllm-local-145/v1`. `api_key` may stay empty: vLLM does
not check it, and suwgit sends `Bearer dummy` so a proxy that insists on a
header is still satisfied.

## Where things live

| `max_diff_chars` | how much diff the model may see, `400_000` by default |

| what        | where |
|-------------|-------|
| config      | `~/.config/suwgit/config.json` — in dotfiles mode a stow symlink into `~/.dotfiles/fedora/.config/suwgit/` |
| API key     | `~/.local/state/suwgit/api_key`, chmod 600, **never** in the config file (that file may be tracked by git) |
| log         | `~/.local/state/suwgit/suwgit.log`, one file, hard-capped at 5 MB |
| locks       | `~/.local/state/suwgit/locks/` |
| unit        | `~/.config/systemd/user/suwgit.service` |

## What it deliberately does not do

- **It does not push.** A remote is required at `register` time as a sanity
  check (an unsynced scratch directory should not be auto-committed), but the
  commits stay local — pushing is yours to decide.
- **It stays quiet.** The daemon never writes to a terminal. An unreachable
  vLLM server is a `WARNING` in the log and nothing else; the changes are left
  uncommitted and picked up on the next sweep.
- **It does not retry.** A failed sweep is not worth hammering a busy GPU for —
  the changes are still there in 1.5 h.
- **It never commits into a mess.** A repository in the middle of a merge,
  rebase, cherry-pick or bisect is skipped, and one repository at a time is
  touched (`flock`), so the daemon and a manual `suwgit commit` cannot race.

## Development

```bash
./scripts/test-solution.sh    # ruff + pytest
```

No runtime dependencies: `bin/suwgit` runs the package on the system `python3`,
so the daemon does not care whether a venv is healthy.
