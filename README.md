# Preflight

Preflight stores your failures, turns them into a checklist for your next project, and blocks the project from starting until the required items are resolved. See `docs/requirements.md` and `docs/design.md`.

Build phases 1 to 3 are in place: sign-in, capture, and the checklist gate. AI review, link capture, progress metrics, and drills come next.

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
uv run preflight my-project start
uv run preflight my-project fail "Retry loop hung"
```

To sign in without a browser, run `preflight login --password`. It asks for the password with hidden input and works only over HTTPS or localhost.

## Check it

```sh
uv run pytest                     # unit and API tests
uv run python scripts/e2e.py      # real server, real CLI, real dashboard forms
```

Both use SQLite by default. To run them against Postgres, set `PREFLIGHT_TEST_DATABASE_URL` or `PREFLIGHT_E2E_DATABASE_URL` to an empty database, such as `postgresql+psycopg://postgres@127.0.0.1:5432/preflight_test`. The tests drop and recreate every table.

## Configuration

The server and CLI read `PREFLIGHT_*` environment variables. `.env.example` lists them. In production, set `PREFLIGHT_REQUIRE_HTTPS=true` and point `PREFLIGHT_DATABASE_URL` at Postgres.

## Behind a proxy

Login lockout and rate limits are counted per client address. Behind a reverse proxy, such as on Render, every request arrives from the proxy's address unless uvicorn trusts the proxy's `X-Forwarded-For` header. Without that, one attacker can rate-limit every visitor. Start the server with:

```sh
uvicorn preflight.server.asgi:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips "<proxy address or range>"
```

Set `--forwarded-allow-ips` to your host's proxy addresses. Use `*` only if the service cannot be reached except through that proxy. Check your host's documentation for which applies.
