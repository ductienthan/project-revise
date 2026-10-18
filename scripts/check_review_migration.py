"""Prove the reviews migration carries ai_call_id into ai_call_ids and back. Uses PREFLIGHT_DATABASE_URL."""

import subprocess
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from preflight.config import Settings


def alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], check=True, capture_output=True)


def main() -> None:
    engine = sa.create_engine(Settings().database_url)
    if sa.inspect(engine).get_table_names():
        raise SystemExit("Refusing to run: this check drops every table, so point PREFLIGHT_DATABASE_URL at an empty database.")
    alembic("upgrade", "head")
    alembic("downgrade", "base")
    alembic("upgrade", "a1f1d57c3f40")
    user, project, call, review = (uuid.uuid4() for _ in range(4))
    now = datetime(2026, 10, 1, 9, tzinfo=UTC)
    rows = {
        "users": {"id": user, "username": "me", "password_hash": "x", "totp_last_step": 0, "created_at": now},
        "projects": {"id": project, "user_id": user, "name": "p", "idea": "", "goals": "", "status": "draft", "created_at": now},
        "ai_calls": {"id": call, "user_id": user, "kind": "review", "prompt_version": "v1", "model": "m", "approved_items": [],
                     "payload": "{}", "response": "", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.1, "error": "", "created_at": now},
        "reviews": {"id": review, "user_id": user, "project_id": project, "approved_items": [], "ai_call_id": call,
                    "drafts": [], "accepted_drafts": [], "created_at": now},
    }
    metadata = sa.MetaData()
    metadata.reflect(engine)
    stored = (lambda v: v.hex) if engine.dialect.name == "sqlite" else (lambda v: v)
    with engine.begin() as c:
        for table, row in rows.items():
            values = {k: stored(v) if isinstance(v, uuid.UUID) else v for k, v in row.items()}
            c.execute(metadata.tables[table].insert().values(**values))
    alembic("upgrade", "head")
    reviews = sa.table("reviews", sa.column("id", sa.Uuid()), sa.column("ai_call_ids", sa.JSON()))
    with engine.connect() as c:
        ids = c.execute(sa.select(reviews.c.ai_call_ids)).scalar_one()
    assert [uuid.UUID(i) for i in ids] == [call], f"upgrade copied {ids}"
    alembic("downgrade", "-1")
    old = sa.table("reviews", sa.column("ai_call_id", sa.Uuid()))
    with engine.connect() as c:
        back = c.execute(sa.select(old.c.ai_call_id)).scalar_one()
    assert back == call, f"downgrade restored {back}"
    alembic("upgrade", "head")
    print("review migration keeps the AI call link in both directions")


if __name__ == "__main__":
    main()
