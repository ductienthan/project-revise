#!/usr/bin/env bash
# Run before every commit. Stops at the first failing step.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src tests scripts
uv run pytest -q
uv run python scripts/e2e.py | tail -1
