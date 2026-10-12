# suwgit

**Your working tree, committed for you, under a name a local LLM wrote.**

Register a repository and forget about it. Every 1.5 hours a background daemon
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
| `suwgit register <folder>` | watch a repository (it must have a git remote); run it again to change its settings — `--default-b2b <tenant>` / `--no-default-b2b`, `--default-project <name>`, `--auto-commit` / `--no-auto-commit` |
| `suwgit unregister <folder>` | stop watching it |
| `suwgit list` | config, service state, and which repositories are dirty |
| `suwgit commit [path]` | commit the closest repository above `path` now, and print the message (`--push` / `--no-push` override the config for one run; `--b2b [tenant]` and `--project [name]` make it a B2B commit, `-y` skips the confirmation) |
| `suwgit push [path]` | the same thing with the push forced on |
| `suwgit report --tenant <tenant>` | a PDF of your B2B commits for one client (`--from` / `--to` as `YYYY-MM-DD`, default: the last 30 days, today included; `-o` for a file or a folder, default: the current folder) |
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

## It stops before committing a secret

Every time it looks at your changes, the model is also asked whether they are
safe to commit at all. If it spots an API key, an access token, a password, a
private key or a filled-in `.env`, **nothing is committed and nothing is
pushed** — the changes stay in your working tree and the reason goes to the log:

```
WARNING  ~/projects/api: REFUSED to commit — possible secret in the changes
         (secrets/prod.env contains a real OpenAI API key in the added lines)
```

A secret in a git history is not undone by a later commit, and the daemon
commits while you are not watching, so this is the one moment anything can stop
it. Placeholders, `.env.example` files and variables merely *named* `api_key`
are left alone.

Treat it as a safety net, not a guarantee: it is a language model's judgement,
so it will not catch everything. Keep your `.gitignore` honest.

## Commits as a record of work (B2B)

Working for a client on a B2B contract, your commit history can double as the
record that the work was done. A commit becomes a record when you ask for one,
with the client's **tenant**, a short working name of your choosing:

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

- **Summary** and **Work-Scope** are written by the model for a client who is
  not technical: what changed for them and their users, in plain words, with no
  jargon, file names or code. Summary is one or two sentences, and Work-Scope
  lists the pieces of work behind it, one item each. The title stays technical,
  for you and `git log`. B2B mode requires both: if the model leaves one out,
  nothing is committed and the reason goes into `.suwgit.log`.
- **Project** is what the client sees. The report has one section per
  project. Without `--project` it is the folder name.
- **Tenant** is your key for filtering. It is never printed in the report.
- **Work-Period** runs from the oldest modification time among the committed
  files to the newest plus two minutes, never later than the moment of the
  commit. It is an ISO 8601 interval with the UTC offset in it. A file saved
  twice only remembers the second save, so the start is an estimate on the
  late side.

In the report, the Summary takes the place of the technical title.

`Project`, `Tenant` and `Work-Period` are ordinary git trailers, so
`git log --format='%(trailers)'` reads them too.

By hand, a commit is B2B only with `--b2b`: in a repository shared with work
for other clients, whether a change is client work is yours to say, one commit
at a time.

### Defaults per repository

So you do not have to type the client every time, a repository can remember
what a bare `--b2b` and `--project` stand for:

```bash
suwgit register ~/projects/shop --default-b2b lokum --default-project "Lokum Shop"
```

| in that repository | commits as |
|---|---|
| `suwgit commit` | an ordinary commit, as always |
| a daemon sweep | B2B for `lokum`, project `Lokum Shop` |
| `suwgit commit --b2b` | B2B for `lokum`, project `Lokum Shop` |
| `suwgit push --b2b acme` | B2B for `acme`, project `Lokum Shop` |
| `suwgit push --b2b acme --project "Acme CRM"` | B2B for `acme`, project `Acme CRM` |

With no default set, a bare `--b2b` falls back to the tenant and project of
the last `--b2b` commit you made by hand in that repository, and asks first:

```console
$ suwgit push --b2b
Commit and push shop as B2B for lokum, project Lokum Shop (last used here)? [Y/n]
```

Enter means yes. `-y` / `--yes` skips the question, and with no terminal to
answer it nothing is committed. The last values are kept per repository in
`~/.local/state/suwgit/last-b2b.json`. A different tenant never inherits the
last project: `--b2b acme` after a commit for `lokum` gets the folder name,
not Lokum's project.

A bare `--b2b` or `--project` with nothing to fall back on stops before
anything is committed and tells you both ways out: name the value, or set the
default. Without a default project, leaving `--project` out gives the
folder name. `--no-default-b2b` and `--default-project ''` forget the defaults.

Write the path before the flags (`suwgit commit ~/projects/shop --b2b`):
a bare `--b2b` would otherwise take the path for a tenant name.

A daemon sweep commits as a bare `--b2b` would wherever a default tenant is
set, so work in a client's repository is recorded even when nobody asked. A
repository shared with other work is better registered with
`--no-auto-commit` and committed by hand, so the daemon never files someone
else's work under the client.

### The report

```bash
suwgit report --tenant lokum --from 2026-09-13 --to 2026-10-13
```

This writes `lokum--<today>.pdf` (e.g. `lokum--2026-10-13.pdf`) in the current
folder, or wherever `-o` points, a file or a folder. It holds a title block, a
summary per project, then each project's commits, each with its Work period,
Summary, Work scope and commit hash. All of it is client-facing; the
technical title is left out. The second and every later project start
on a new page. Dates are ISO only, because `9/10/2026` means a different
month depending on who reads it. A commit counts when its work started within
the range, both ends included.

There is no database. Each B2B commit adds its repository to
`~/.local/state/suwgit/tenants.json`, and the report reads the git history of
every repository listed there for that tenant, on every branch. Only commits
under your own `user.email` count, so in a shared repository a colleague's
commits are not in your report. The index only says where to look. Losing it
loses no work: the history is the record.

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
  "repos": [
    {"path": "/home/you/projects/shop", "auto_commit": true, "default_tenant": "lokum", "default_project": "Lokum Shop"}
  ]
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
| `repos` | written by `register`; `auto_commit: false` keeps the daemon away; `default_tenant` / `default_project` are what a bare `--b2b` / `--project` mean there. A bare path, as older versions wrote it, still works |

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
| tenant index | `~/.local/state/suwgit/tenants.json`: which repositories hold B2B commits for which tenant. Kept by `uninstall`, removed by `--purge` |
| last `--b2b` | `~/.local/state/suwgit/last-b2b.json`: the tenant and project last used by hand, per repository |
| blocker notes | `.suwgit.log` in the repository itself — see below |
| unit | `~/.config/systemd/user/suwgit.service` |

## When it cannot commit

A daemon that fails silently is a daemon you stop trusting. So when something
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
- **It does not commit while you are still working.** A sweep leaves a
  repository alone if any uncommitted file was touched in the last three
  quarters of the interval — 67 minutes at the default 1.5 hours. Committing
  mid-edit gives you half a refactor under a message written about work that is
  not done yet. Nothing is lost by waiting: the next sweep sees the same changes
  plus whatever you added. `suwgit commit` by hand ignores this entirely —
  asking for a commit is the statement that you are finished.
- **It never commits into a mess.** A repository in the middle of a merge,
  rebase, cherry-pick or bisect is left alone until you have finished. The
  daemon and a manual `suwgit commit` can never collide over the same
  repository.

## License

[MIT](LICENSE)
