# AGENTS.md

Rules for anyone — human or AI agent — changing this repository. `CLAUDE.md` only points
here; this file is the one to edit.

suwgit runs on Linux (systemd) and on Windows (Task Scheduler) from **one codebase**. Everything
is shared except two small seams chosen at runtime by the OS: `suwgit/host.py` and
`suwgit/install.py` (section 2). How the two branches became one: [docs/windows-linux-merge.md](docs/windows-linux-merge.md).

## Git: committing is the owner's job

**Agents never commit, amend, reset, rebase or push** — nor stage files (`git add`,
`git mv`). Leave every change in the working tree for the owner to review and commit, even
when it is finished and all checks pass. The only exception is the owner asking for it in so
many words, and it covers that task only (then through `suwgit commit` / `suwgit push`).

## 0. KISS & YAGNI — the first rule

- **Standard library only.** The background job runs a bare `python3` / `pythonw.exe`; no venv
  has to stay healthy for it. `pytest` is a dev dependency and nothing else is.
- **No configuration for scenarios that do not exist yet**, no abstraction before the
  second use, and prefer deleting to adding.
- **When in doubt, leave it out** — and say in the change what was left out and why.

## 1. What lives where

| Path | What |
| --- | --- |
| `suwgit/committer.py`, `gitops.py`, `llm.py`, `repolog.py`, `config.py`, `daemon.py` | the tool itself: what to commit, how to name it. Platform-independent |
| `suwgit/b2b.py`, `report.py`, `tenants.py` | B2B commits as a record of client work, and `suwgit report`. Platform-independent |
| `suwgit/host.py` | seam 1, the OS primitives: the file lock (`flock` / `msvcrt`), `chmod 600`, "no console window" for subprocesses |
| `suwgit/install.py`, `install_systemd.py`, `install_windows.py` | seam 2: `suwgit` on PATH and the scheduler. `install.py` hands each call to the systemd or the Windows implementation, which offer the same functions |
| `suwgit/locking.py`, `logs.py`, `paths.py` | the lock, the capped log and the file locations: shared, built on `host.py` |
| `suwgit/initcmd.py`, `uninstall.py` | setup and removal; ask `install.py`, never the OS |
| `bin/suwgit.py` | the entry point every launcher, the unit and the task run; `bin/suwgit` runs it from the clone |
| `.github/workflows/ci.yml` | the gate on `ubuntu-latest` and `windows-latest` |
| `scripts/.internal/` | logic the `justfile` calls; `scripts/test-solution.sh` only calls `check.sh` |

## 2. Linux and Windows

- **Platform-specific code lives in the two seams only.** Anything else that needs
  `sys.platform` is a sign the seam is missing a function. Ask the OS through
  `host.is_windows()` (a function, so tests can patch it), never by importing a platform module at the top
  (`fcntl`, `msvcrt` are imported inside the function that uses them).
- **Every subprocess gets `**host.no_window()`.** The Windows sweep runs under `pythonw` with
  no console; a missing flag flashes a window every interval.
- **Every subprocess reading git output decodes it as UTF-8** (`gitops._git_bytes` reads bytes), never the
  ANSI code page.
- **Never write into a user's repository in text mode** — Windows turns `\n` into `\r\n`
  and the whole file shows as changed. Append bytes in the file's own line ending
  (`repolog.ignore_entry`).
- **No rename over a file someone may hold open** (the log, while `suwgit logs -f` runs) —
  Windows refuses it. Rewrite in place under the lock instead.
- **State lives under `~/.config` and `~/.local/state` on both systems, never `%APPDATA%`** — the
  Store build of Python virtualises writes there.
- **Tests never touch the real systemd or Task Scheduler.** The `sandbox` fixture points every
  path elsewhere and renames the task. The Windows implementations are tested on Linux too, by
  patching `host.is_windows` or `subprocess`, not the logic; what needs Windows itself is
  `pytest.mark.skipif(sys.platform != "win32", ...)`. Anything that registers a task for real is a
  manual check, cleaned up afterwards.
- A new feature works on both systems or says in the change why not.

## 3. Commands: `justfile` vs `scripts/.internal/`

- **`justfile` is for humans** — every setup step is a recipe there, grouped `setup`,
  `schedule`, `maintenance`; OS-specific ones carry `[linux]` / `[windows]`. A new human-facing
  command goes there, in the matching group.
- **Logic longer than a line or two** lives in `scripts/.internal/` as a `.sh` (or in the
  package), and the recipe only calls it.
- **Agents call these directly:**

  | Script | Use it to |
  | --- | --- |
  | `scripts/.internal/check.sh` | verify a change — ruff, format, lockfile, pytest, shellcheck |

- Calling `schtasks` from bash needs `MSYS_NO_PATHCONV=1`, or Git Bash turns `/Run` into a
  path.

## 4. The quality gate

`just check` must be green before a change is done; CI runs the same script on Linux and Windows.
A new behaviour brings its test along: "fix the bug" means a test that reproduces it first.

## 5. Behavioral guidelines

- **Think before coding.** State assumptions; if something is unclear, stop and ask.
- **Surgical changes.** Touch only what the task needs; match the existing style; mention
  unrelated dead code rather than deleting it.
- **Goal-driven.** Define the check that proves the task done, and loop until it passes.
