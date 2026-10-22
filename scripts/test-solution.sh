#!/usr/bin/env bash
# Brama jakości: to samo co `just check` — jedna brama dla Linuksa i Windowsa.
exec "$(dirname "$0")/.internal/check.sh" "$@"
