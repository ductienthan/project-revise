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

## Configuration

The server and CLI read `PREFLIGHT_*` environment variables. `.env.example` lists them. In production, set `PREFLIGHT_REQUIRE_HTTPS=true` and point `PREFLIGHT_DATABASE_URL` at Postgres.
