---
description: Update suwgit and this plugin to the latest version from GitHub
argument-hint: "[optional: tool | plugin — default both]"
allowed-tools: Bash(command -v suwgit), Bash(readlink:*), Bash(git -C:*), Bash(systemctl --user restart suwgit), Bash(systemctl --user is-active suwgit), Bash(claude plugin marketplace update:*), Bash(claude plugin update:*)
---

Update suwgit to its latest version, as the user asks:

$ARGUMENTS

Two parts, both by default; "tool" or "plugin" means only that one.

**The tool** — the suwgit clone that `suwgit` on PATH runs from:

1. Find the clone: `readlink -f "$(command -v suwgit)"` ends in `<clone>/bin/suwgit`
   on Linux. On Windows the launcher is a small generated script: read the clone
   path from it, or ask the user.
2. `git -C <clone> status --porcelain` and `git -C <clone> branch --show-current`.
   Pull only when the clone is clean and on `master` (Linux) or `master-windows`
   (Windows): `git -C <clone> pull --ff-only`. If it has local changes, is on
   another branch, or cannot fast-forward, change nothing and tell the user why —
   it may be their own working copy.
3. On Linux, after a pull that changed something, restart the background
   service so it runs the new code: `systemctl --user restart suwgit`, then
   `systemctl --user is-active suwgit`. On Windows nothing is needed: each
   scheduled sweep starts the latest code.

**The plugin** — this skill and these commands, a copy Claude Code keeps:

1. `claude plugin marketplace update suwalski-git`
2. `claude plugin update suwgit@suwalski-git`
3. Tell the user to run `/reload-plugins` (or restart Claude Code) for the new
   version to take effect in this session.

Report what changed: the commits the pull brought (from its output), and
whether the plugin moved to a new version or was already the latest.
