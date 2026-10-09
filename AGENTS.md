# AGENTS.md

Rules for anyone — human or AI agent — changing this repository. `CLAUDE.md` only points
here; this file is the one to edit.

suwgit for Windows: a port of [suwalski-git](https://github.com/Manomenu/suwalski-git)
(remote `upstream`) that runs under the Task Scheduler instead of systemd. The commit logic
is shared with upstream on purpose — keep the platform layer thin, so upstream fixes still
apply.

## Git: committing is the owner's job

**Agents never commit, amend, reset, rebase or push** — nor stage files (`git add`,
`git mv`). Leave every change in the working tree for the owner to review and commit, even
when it is finished and all checks pass. The only exception is the owner asking for it in so
many words, and it covers that task only.

## 0. KISS & YAGNI — the first rule

- **Standard library only.** The scheduled task runs a bare `pythonw.exe`; no venv has to
  stay healthy for it. `pytest` is a dev dependency and nothing else is.
- **No configuration for scenarios that do not exist yet**, no abstraction before the
  second use, and prefer deleting to adding.
- **When in doubt, leave it out** — and say in the change what was left out and why.

## 1. What lives where

| Path | What |
| --- | --- |
| `suwgit/committer.py`, `gitops.py`, `llm.py`, `repolog.py`, `config.py` | the tool itself: what to commit, how to name it — mostly identical to upstream |
| `suwgit/sweep.py` | one pass over the registry — what the scheduled task runs |
| `suwgit/install.py`, `initcmd.py`, `uninstall.py` | launchers in `~/.local/bin` and the Task Scheduler entry |
| `suwgit/locking.py`, `logs.py`, `paths.py` | the Windows platform layer: `msvcrt` locks, the capped log, file locations |
| `bin/suwgit.py` | the entry point every launcher and the task run; `bin/suwgit` runs it from the clone |
| `scripts/.internal/` | logic the `justfile` calls |

## 2. Windows rules the code depends on

- **Every subprocess gets `creationflags=subprocess.CREATE_NO_WINDOW`.** The sweep runs
  under `pythonw` with no console; a missing flag flashes a window every interval.
- **Every subprocess reading git output passes `encoding="utf-8"`.** The default is the ANSI
  code page, which garbles Polish letters and chokes on some bytes. Python itself runs with
  `-X utf8` (launchers and task), but the subprocess decoding must not rely on it.
- **Never write into a user's repository in text mode** — Windows turns `\n` into `\r\n`
  and the whole file shows as changed. Append bytes in the file's own line ending
  (`repolog.ignore_entry`).
- **No rename over a file someone may hold open** (the log, while `suwgit logs -f` runs) —
  Windows refuses it. Rewrite in place under the lock instead.
- **State lives under `~/.config` and `~/.local/state`, never `%APPDATA%`** — the Store
  build of Python virtualises writes there.
- **Tests never touch the real Task Scheduler.** The `sandbox` fixture renames the task;
  anything that registers one for real is a manual check, cleaned up afterwards.

## 3. Commands: `justfile` vs `scripts/.internal/`

- **`justfile` is for humans** — every setup step is a recipe there, grouped `setup`,
  `schedule`, `maintenance`. A new human-facing command goes there, in the matching group.
- **Logic longer than a line or two** lives in `scripts/.internal/` as a `.sh` (or in the
  package), and the recipe only calls it.
- **Agents call these directly:**

  | Script | Use it to |
  | --- | --- |
  | `scripts/.internal/check.sh` | verify a change — ruff, format, lockfile, pytest, shellcheck |

- Calling `schtasks` from bash needs `MSYS_NO_PATHCONV=1`, or Git Bash turns `/Run` into a
  path.

## 4. The quality gate

`just check` must be green before a change is done. A new behaviour brings its test along:
"fix the bug" means a test that reproduces it first.

## 5. Behavioral guidelines

- **Think before coding.** State assumptions; if something is unclear, stop and ask.
- **Surgical changes.** Touch only what the task needs; match the existing style; mention
  unrelated dead code rather than deleting it.
- **Goal-driven.** Define the check that proves the task done, and loop until it passes.
