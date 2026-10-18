#!/usr/bin/env bash
# Run before every commit. Stops at the first failing step.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src tests scripts
PREFLIGHT_DATABASE_URL="sqlite:///$(mktemp -d)/migration.db" uv run python scripts/check_review_migration.py
uv run python scripts/validate_requirements.py
uv run python scripts/mutation_check.py | tail -1
