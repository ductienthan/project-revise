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
uv run preflight my-project close           # retrospective
uv run preflight drill                      # practice drills from past failures
uv run preflight report --week
```

Run `preflight --help` and `preflight <project> --help` for every command.

To sign in without a browser, run `preflight login --password`. It asks for the password with hidden input and works only over HTTPS or localhost.

## Check it

```sh
./scripts/check.sh                # lint, tests, and the end-to-end check; run before every commit
```

`scripts/e2e.py` runs the real migration, server, CLI binary, and dashboard forms. It points the server at a local fake of the Anthropic Messages API, so it needs no API key and spends nothing.

Both use SQLite by default. To run them against Postgres, set `PREFLIGHT_TEST_DATABASE_URL` or `PREFLIGHT_E2E_DATABASE_URL` to an empty database, such as `postgresql+psycopg://postgres@127.0.0.1:5432/preflight_test`. The tests drop and recreate every table.

## Configuration

The server and CLI read `PREFLIGHT_*` environment variables. `.env.example` lists them. In production, set `PREFLIGHT_REQUIRE_HTTPS=true` and point `PREFLIGHT_DATABASE_URL` at Postgres.

AI features need `PREFLIGHT_ANTHROPIC_API_KEY`. Without it, everything except the AI review, AI suggestions, and AI drill grading still works. Reviews use Claude Opus 5.5 and the smaller tasks use Claude Haiku 5.5 (`PREFLIGHT_REVIEW_MODEL`, `PREFLIGHT_SMALL_MODEL`). `PREFLIGHT_AI_MONTHLY_BUDGET_USD` (default 5) stops AI calls once the month's logged spend reaches it. Set a matching spend limit in the Anthropic Console as well.

Link capture fetches public pages only. Each request connects to an address that was checked as public, so it cannot be turned against the server's own network. Pages that need JavaScript are skipped unless you set `PREFLIGHT_CAPTURE_BROWSER=true` and install the browser extra (`uv sync --extra browser`, then `uv run playwright install chromium`). The browser does its own DNS lookups, so its address check cannot pin addresses the same way. Leave it off on an internet-facing server unless you need it.

## Behind a proxy

Login lockout and rate limits are counted per client address. Behind a reverse proxy, such as on Render, every request arrives from the proxy's address unless uvicorn trusts the proxy's `X-Forwarded-For` header. Without that, one attacker can rate-limit every visitor. Start the server with:

```sh
uvicorn preflight.server.asgi:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips "<proxy address or range>"
```

Set `--forwarded-allow-ips` to your host's proxy addresses. Use `*` only if the service cannot be reached except through that proxy. Check your host's documentation for which applies.
