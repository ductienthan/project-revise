"""reviews span several AI calls

Revision ID: 37d73a80af00
Revises: a1f1d57c3f40
Create Date: 2026-10-17 17:27:32.425350
"""
import uuid

from alembic import op
import sqlalchemy as sa

import preflight.db.base


revision = '37d73a80af00'
down_revision = 'a1f1d57c3f40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reviews", schema=None) as batch_op:
        batch_op.add_column(sa.Column("material_hash", sa.String(length=64), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("ai_call_ids", sa.JSON(), nullable=False, server_default="[]"))

    reviews = sa.table("reviews", sa.column("id", sa.Uuid()), sa.column("ai_call_id", sa.Uuid()), sa.column("ai_call_ids", sa.JSON()))
    bind = op.get_bind()
    for review_id, call_id in bind.execute(sa.select(reviews.c.id, reviews.c.ai_call_id)).all():
        bind.execute(reviews.update().where(reviews.c.id == review_id).values(ai_call_ids=[str(call_id)]))

    with op.batch_alter_table("reviews", schema=None) as batch_op:
        batch_op.drop_constraint("fk_reviews_ai_call_id", type_="foreignkey")
        batch_op.drop_column("ai_call_id")


def downgrade() -> None:
    with op.batch_alter_table("reviews", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ai_call_id", sa.Uuid(), nullable=True))

    reviews = sa.table("reviews", sa.column("id", sa.Uuid()), sa.column("ai_call_id", sa.Uuid()), sa.column("ai_call_ids", sa.JSON()))
    bind = op.get_bind()
    for review_id, call_ids in bind.execute(sa.select(reviews.c.id, reviews.c.ai_call_ids)).all():
        if call_ids:
            bind.execute(reviews.update().where(reviews.c.id == review_id).values(ai_call_id=uuid.UUID(call_ids[0])))

    with op.batch_alter_table("reviews", schema=None) as batch_op:
        batch_op.alter_column("ai_call_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.create_foreign_key("fk_reviews_ai_call_id", "ai_calls", ["ai_call_id"], ["id"])
        batch_op.drop_column("ai_call_ids")
        batch_op.drop_column("material_hash")
