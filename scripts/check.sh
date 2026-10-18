#!/usr/bin/env bash
# Run before every commit. Stops at the first failing step.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src tests scripts
uv run pytest -q
PREFLIGHT_DATABASE_URL="sqlite:///$(mktemp -d)/migration.db" uv run python scripts/check_review_migration.py
uv run python scripts/e2e.py | tail -1
