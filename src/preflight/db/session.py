from collections.abc import Callable
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from preflight.db.base import utcnow


def make_engine(url: str) -> Engine:
    if url in ("sqlite://", "sqlite:///:memory:"):
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    elif url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
    else:
        return create_engine(url, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def make_sessionmaker(engine: Engine, clock: Callable[[], datetime] = utcnow) -> sessionmaker:
    """Stamp created_at from the app's clock, so stored times and domain logic share one notion of now."""
    factory = sessionmaker(engine, expire_on_commit=False)

    @event.listens_for(factory, "before_flush")
    def _stamp(session, _context, _instances):
        for obj in session.new:
            if hasattr(obj, "created_at") and obj.created_at is None:
                obj.created_at = clock()

    return factory
