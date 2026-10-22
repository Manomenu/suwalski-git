# Entry point for humans: `just` lists every recipe, grouped. Logic longer than a line or
# two lives in scripts/.internal/ (or in the suwgit package itself), which is also what
# agents call directly.
#
# Linux and Windows alike. On Windows, run it from Git Bash, cmd or PowerShell: `just` runs
# every recipe in Git Bash.

set shell := ["bash", "-euo", "pipefail", "-c"]

[private]
default:
    @just --list --unsorted

# ---------------------------------------------------------------------------------------
# setup — put suwgit on this machine, or take it off
# ---------------------------------------------------------------------------------------

# Interactive: LLM server, interval, schedule, push → config, suwgit on PATH, systemd unit (Linux) or scheduled task (Windows). Re-runnable
[group('setup')]
install:
    @./bin/suwgit init

# Remove suwgit from PATH, the systemd unit or scheduled task, and the logs; the config stays unless --purge
[group('setup')]
uninstall *args:
    @./bin/suwgit uninstall {{ args }}

# ---------------------------------------------------------------------------------------
# schedule — what runs suwgit on a clock: systemd on Linux, the Task Scheduler on Windows
# ---------------------------------------------------------------------------------------

# Everything Windows knows about the task: state, last result, next run
[group('schedule')]
[windows]
task:
    @MSYS_NO_PATHCONV=1 schtasks /Query /TN suwgit /V /FO LIST

# Start a sweep through the scheduler now — the real, windowless path; results in `suwgit logs`
[group('schedule')]
[windows]
task-run:
    @MSYS_NO_PATHCONV=1 schtasks /Run /TN suwgit

# The systemd user unit: state and its latest log lines
[group('schedule')]
[linux]
service:
    @systemctl --user status suwgit.service --no-pager

# ---------------------------------------------------------------------------------------
# maintenance — quality gate and housekeeping
# ---------------------------------------------------------------------------------------

# Lint, format check, tests, shell scripts, lockfile — run before every commit
[group('maintenance')]
check:
    @./scripts/.internal/check.sh

# Apply ruff's autofixes and formatting
[group('maintenance')]
fmt:
    uvx ruff check --fix .
    uvx ruff format .

# Install/refresh the dev dependencies (pytest) from the lockfile
[group('maintenance')]
sync:
    uv sync
