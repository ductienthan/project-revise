from alembic import context
from sqlalchemy import engine_from_config, pool

from preflight.config import settings
from preflight.db import models  # noqa: F401
from preflight.db.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_online() -> None:
    engine = engine_from_config(config.get_section(config.config_ini_section), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
