#!/usr/bin/env bash
# Lint, format check, tests and shell scripts, with a summary report.
# Exit code is non-zero if any step failed. Humans run `just check`; agents run this.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

declare -a REPORT
FAILED=0

run_step() {
    local name="$1"; shift
    echo
    echo "==> $name"
    if "$@"; then
        REPORT+=("PASS  $name")
    else
        REPORT+=("FAIL  $name")
        FAILED=1
    fi
}

lint() { uvx ruff check .; }
# Does not rewrite anything — `just fmt` does.
format_check() { uvx ruff format --check .; }
lockfile() { uv lock --check; }
tests() { env -u VIRTUAL_ENV uv run pytest -q; }
shell_scripts() { shellcheck -S warning bin/suwgit scripts/.internal/*.sh; }

run_step "lint (ruff)" lint
run_step "format (ruff format --check)" format_check
run_step "lockfile up to date (uv lock --check)" lockfile
run_step "tests (pytest)" tests

# A tool a fresh machine may lack is skipped with a visible note instead of a PASS — a
# silently skipped step is worse than no step.
if command -v shellcheck >/dev/null 2>&1; then
    run_step "shell scripts (shellcheck)" shell_scripts
else
    REPORT+=("SKIP  shell scripts — shellcheck not installed (winget install koalaman.shellcheck)")
fi

echo
echo "==================== check report ===================="
for line in "${REPORT[@]}"; do
    echo "  $line"
done
echo "======================================================"
exit "$FAILED"
