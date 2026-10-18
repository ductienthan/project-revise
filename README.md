# Preflight

Preflight stores your failures, turns them into a checklist for your next project, and blocks the project from starting until the required items are resolved. See `docs/requirements.md` and `docs/design.md`.

All seven build phases from `docs/design.md` are in place: sign-in, failure capture, the checklist gate, the AI review, link capture, progress metrics, and drills. Deployment is not set up yet.

## Run it locally

```sh
uv sync
uv run alembic upgrade head
uv run preflight-server create-user --username me
uv run uvicorn preflight.server.asgi:app --reload
```

In another terminal:

```sh
uv run preflight login            # confirm the code in the browser
uv run preflight new my-project
uv run preflight my-project add-link https://example.com/docs --capture
uv run preflight my-project start           # approve what AI sees, answer its questions, work the gate
uv run preflight my-project fail "Retry loop hung"
uv run preflight my-project review          # review again after requirements or links change
uv run preflight my-project close           # retrospective
uv run preflight drill                      # practice drills from past failures
uv run preflight report --week
uv run preflight import old-failures.csv    # backfill: project,title,date plus optional fields
```

Run `preflight --help` and `preflight <project> --help` for every command.

To sign in without a browser, run `preflight login --password`. It asks for the password with hidden input and works only over HTTPS or localhost.

## Check it

```sh
./scripts/check.sh                # run before every commit, about two minutes
```

It runs four things, and stops at the first failure:

1. `ruff` on the source, tests, and scripts.
2. `scripts/check_review_migration.py`, which proves the data migration round trip on a throwaway database.
3. `scripts/validate_requirements.py`, which runs the test suite and the end-to-end check once and checks every requirement against them. `validation/requirements.json` maps each requirement in `docs/requirements.md` and `docs/design.md` to the tests and end-to-end checks that prove it. The script fails when a requirement in the document has no entry, when an entry marked met has no evidence or cites a test that does not exist, or when any cited evidence fails. Open and superseded items must give a reason. It writes `validation/report.md`.
4. `scripts/mutation_check.py`, which breaks 20 protections on purpose (the start gate, the AI approval gate, CSRF, data separation between accounts, the private-address guard, and others) in a throwaway copy of the code and confirms the tests catch every one.

When you add or change a requirement, add or update its entry in `validation/requirements.json`. When you add a protection, add a mutation for it.

`scripts/e2e.py` runs the real migration, server, CLI binary, and dashboard forms. It points the server at a local fake of the Anthropic Messages API, so it needs no API key and spends nothing.

Both use SQLite by default. To run them against Postgres, set `PREFLIGHT_TEST_DATABASE_URL` or `PREFLIGHT_E2E_DATABASE_URL` to an empty database, such as `postgresql+psycopg://postgres@127.0.0.1:5432/preflight_test`. The tests drop and recreate every table.

## Configuration

The server and CLI read `PREFLIGHT_*` environment variables. `.env.example` lists them. In production, set `PREFLIGHT_REQUIRE_HTTPS=true` and point `PREFLIGHT_DATABASE_URL` at Postgres.

### AI providers

With `PREFLIGHT_ANTHROPIC_API_KEY` set, reviews use Claude Opus 5.5 and the smaller tasks use Claude Haiku 5.5 (`PREFLIGHT_REVIEW_MODEL`, `PREFLIGHT_SMALL_MODEL`). `PREFLIGHT_AI_MONTHLY_BUDGET_USD` (default 5) refuses any call whose worst-case cost would pass the month's budget. Set a matching spend limit in the Anthropic Console as well.

Without a key, the server uses a local [Ollama](https://ollama.com) at `PREFLIGHT_OLLAMA_URL` (default `http://127.0.0.1:11434`). It picks the largest installed model unless `PREFLIGHT_OLLAMA_MODEL` is set, and splits long material to fit `PREFLIGHT_OLLAMA_CONTEXT_TOKENS` (default 32768). Local calls cost nothing and the data stays on the machine running Ollama. Every item still needs your approval before it is sent. Set `PREFLIGHT_AI_PROVIDER` to `anthropic`, `ollama`, or `off` to choose explicitly; the default `auto` behaves as described.

Without either provider, everything except the AI review, AI suggestions, and AI drill grading still works.

### Checking the review prompt

`tests/golden/review_payload.txt` holds the exact payload for a fixed project, so a prompt change shows up as a diff. Regenerate it with `PREFLIGHT_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py` after an intended change.

`scripts/eval_review.py` runs the real review on the cases in `evals/review_cases.json` against the configured provider and scores how many relevant past failures the review addresses, how many unrelated ones it drags in, how many drafts it drops, and which question categories it covers. Results go to `evals/results/`. With Ollama it costs nothing:

```sh
PREFLIGHT_OLLAMA_MODEL=gemma4:12b uv run python scripts/eval_review.py --trials 3
```

Link capture fetches public pages only. Each request connects to an address that was checked as public, so it cannot be turned against the server's own network. Pages that need JavaScript are skipped unless you set `PREFLIGHT_CAPTURE_BROWSER=true` and install the browser extra (`uv sync --extra browser`, then `uv run playwright install chromium`). Every request the browser makes passes the same public-address check, but the browser does its own DNS lookups, so it cannot pin addresses the way plain capture does. Leave it off on an internet-facing server unless you need it.

## Behind a proxy

Login lockout and rate limits are counted per client address. Behind a reverse proxy, such as on Render, every request arrives from the proxy's address unless uvicorn trusts the proxy's `X-Forwarded-For` header. Without that, one attacker can rate-limit every visitor. Start the server with:

```sh
uvicorn preflight.server.asgi:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips "<proxy address or range>"
```

Set `--forwarded-allow-ips` to your host's proxy addresses. Use `*` only if the service cannot be reached except through that proxy. Check your host's documentation for which applies.
