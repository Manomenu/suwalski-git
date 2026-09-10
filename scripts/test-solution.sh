#!/usr/bin/env bash
# Brama jakości: ruff + pytest. Puszczaj przed każdym commitem.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --with ruff ruff check .
uv run --with ruff ruff format --check .
uv run --with pytest python -m pytest tests -q
