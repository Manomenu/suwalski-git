# Linux and Windows in one codebase

Until this merge suwgit had two masters: `master` (Linux, systemd) and `master-windows`
(Windows, Task Scheduler). Both came from the common base `25f676b` (2026-10-06). This is the
record of what each side had, what was kept, and where the seam between the systems now is.

## What the two branches turned out to be

`master-windows` was a port of `master` at the base, then kept in step by hand: the 13 commits
after the port (`24cf23e` B2B and reports, `de9db7a` AI summaries, `6445766` deduplication and
reverts, `d2e3329`/`2c4d016`/`8ef5561`/`08e4f6b` e-mail and mailboxes and free-text hints,
`160d1fd` push of a clean tree and the ticket id from the branch, `e3defc8` the `-i` checklists,
`beb4bf6` Polish) are each the same feature as the matching `master` commit (`6e8e6cc` ...
`70993c9`), written twice. `b2b.py`, `report.py`, `tenants.py`, `language.py`, `mailer.py`,
`picker.py` are byte-identical on both; so are the Claude plugin and the screenshots.

So **there is no platform-independent feature that only Windows has**. The platform-independent
differences all go one way: Windows is behind `master`. `master` has 21 commits since the base,
Windows 14.

## 1. Genuinely Windows-specific (kept, as the second implementation of a seam)

| Where | Windows | Linux |
| --- | --- | --- |
| Clock | Task Scheduler entry `suwgit` (XML, `schtasks`), runs `suwgit sweep` every interval, windowless `pythonw`, `InteractiveToken` (no stored password), `StartWhenAvailable`, `IgnoreNew`, 2 h limit (`install_windows.py`) | systemd user unit running `suwgit daemon`, PATH of the installing shell baked in (`install_systemd.py`) |
| `suwgit` on PATH | two generated launchers, `suwgit` (Git Bash) and `suwgit.cmd`, carrying the Python that ran `init`; never overwrites a foreign file | symlink to `bin/suwgit` |
| Entry point | `bin/suwgit.py` (a plain script; nothing on `PYTHONPATH`, no venv) | was `bin/suwgit` running `python3 -c`; now `bin/suwgit` is a tiny bash wrapper over `bin/suwgit.py` for both (the installed symlink keeps working) |
| Lock | `msvcrt.locking` byte-range, polled for a blocking lock (`host.py`) | `fcntl.flock` |
| Console window | every subprocess `CREATE_NO_WINDOW` (`host.no_window()`) | none |
| Secrets files | no `chmod 600` (profile folder is private) | `chmod 600` |
| Config location | always `~/.config/suwgit` (never `%APPDATA%`: Store Python) | `init` also offers the GNU Stow dotfiles tree (Linux only; Windows is not asked) |
| `schtasks` output | decoded as the console's OEM code page | n/a |
| Config key | `scheduled` | `open_on_system_start` |

## 2. Platform-independent work done only on `master-windows`, and kept

Small, but real, and better than master's version on both systems:

- **`logs.py`**: `suwgit logs` in pure Python (last lines, `-f` that notices the in-place trim),
  `bat` only when installed. Master shelled out to `tail`. The log lock is now the shared
  `file_lock`.
- **`repolog.ignore_entry`**: appends to the user's `.gitignore` as bytes, in the file's own
  line ending. Master rewrote the file in text mode (harmless on Linux, rewrites every line
  of a CRLF checkout on Windows). Tests added by Windows kept.
- **Tests**: `test_logs` with the `spawn` start method (the only one Windows has, works on
  Linux), `test_push` reading the bare remote with `--git-dir`, `test_config` comparing paths
  through `Path`, the CRLF/LF `.gitignore` tests.
- **Repository tooling**: `AGENTS.md`/`CLAUDE.md`, `justfile`, `scripts/.internal/check.sh`
  (ruff, format, lockfile, pytest, shellcheck, with a visible SKIP), `.editorconfig`,
  `.gitattributes` (LF everywhere), a fuller `.gitignore`, pytest `testpaths`.
  `scripts/test-solution.sh` stays, as a one-line call to `check.sh`.
- **`report -o <dir>\`**: a trailing backslash counts as a folder too.

## 3. Duplicated work: which implementation won

| Feature (on both) | Winner | Why |
| --- | --- | --- |
| Everything in `committer.py`, `gitops.py`, `llm.py`, `config.py`, `main.py`, `initcmd.py` outside the platform seam, B2B, reports, e-mail, Polish, `-p`, `-i`, hints, MR link, clean-tree push | master | Same code at the start; master is newer and has the extra tests (`test_gitops`, `test_llm_transport`, more in `test_committer`/`test_review`) |
| Polish language support (`70993c9` / `beb4bf6`) | master (identical content) | Same files, same tests; only the sandbox/Task names differ |
| Sweep: Windows `sweep.py` `run()` vs master `daemon.py` `run_once()` | master's `daemon.py` | `run_once` is the same loop body; `suwgit sweep` (the Task Scheduler's command) is a new one-pass command over it, printing the summary and logging `sweep: done (...)`. `daemon --once` stays |
| Diff handling | master | lockfile/generated/binary/deleted files summarised in one line each, 200,000-character default (Windows still had 400,000 and the plain diff) |
| Revert detection (`exact_revert_of`) | master | cheap numstat prefilter before any full patch |
| Reading git output | master's bytes + `errors="replace"` | survives non-UTF-8 files; Windows' `encoding="utf-8"` was the same intent. The `CREATE_NO_WINDOW` flag was added on top |
| `_anonymous` model URL hiding in errors | master | Windows leaked the server address into logs and `.suwgit.log` |
| Merge-request link on the default branch | master | no link for `main`/`master`, stale `origin/HEAD` handled |
| Finishing a resolved merge (`finish_merge`) | master | Windows lacked it |
| Help texts (`commit`/`push` path argument) | master | longer |
| Locking, log lock, `repolog` | Windows' structure, shared | one `file_lock(path, wait)` in `locking.py` over `host.py`, replacing master's inline `flock` calls |

Nothing either side did was dropped: master's Stow dotfiles mode, `open_on_system_start`, the
`daemon` loop and `daemon --once`, the systemd unit; Windows' task, launchers and `sweep`.

## 4. Master commits Windows lacked (now on Windows too)

`bdfd1ed` (non-UTF-8 git output, cheap revert check, prompt size budget, model URL hidden in
errors, no PR link on the default branch, finishing resolved merges, help text), and
before it the lockfile/generated/binary omission from the diff, the 200,000-character default,
the `merge_request_url` fixes, the `exact_revert_of` rewrite, and the extra tests for them.
(`git log 25f676b..master` lists all 21.)

## The seam

Two places, both chosen at runtime (`host.is_windows()`, a function so tests can patch it):

- `suwgit/host.py`: `no_window()`, `keep_private()`, `try_lock()`/`unlock()`.
- `suwgit/install.py`: hands each call to `install_systemd.py` or `install_windows.py`, which
  offer the same functions (`link_into_path`, `unlink_from_path`, `launchers`,
  `install_schedule`, `uninstall_schedule`, `schedule_status`, `schedule_label`, and the two
  texts `AUTOSTART_QUESTION`, `PATH_HINT`). `initcmd`, `uninstall` and `main` never ask which
  system they are on, apart from `init` skipping the Stow question on Windows.

`paths.py` holds the constants of both (systemd unit and symlink, launchers and task name).
`ENTRYPOINT` (`bin/suwgit`) is what Linux points at; `PYTHON_ENTRYPOINT` (`bin/suwgit.py`)
what Windows runs.

One config key: `open_on_system_start`. The Windows name `scheduled` still loads, so an
existing Windows config keeps its setting (and is rewritten under the new name at the next save).

## Tests

All run on Linux. The Windows implementations are covered where they can be: the task XML, the
launchers, the exact `schtasks` command lines (with `subprocess` replaced), the status parsing,
`no_window()` reaching every git call. Only the `msvcrt` lock test is marked
`skipif(sys.platform != "win32")`. CI runs the gate on both systems.

## Not checked, for a real Windows machine

- Registering the task for real (`suwgit init`, then `schtasks /Query /TN suwgit`) and one
  scheduled run (`schtasks /Run /TN suwgit`, then `suwgit logs`).
- The launchers from Git Bash, cmd and PowerShell; `bin/suwgit` (bash) now runs `bin/suwgit.py`
  with `-X utf8` like before.
- The existing Windows config (key `scheduled`) after a `git pull` of this branch.
- The `msvcrt` lock against two real processes (`test_the_windows_lock_is_a_byte_range_lock`
  only checks two locks in one process; the log test uses four).
