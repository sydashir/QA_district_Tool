"""Engine/session wiring. One place so the URL is not scattered through the code."""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# Local dev default matches the docker container started for this build. Overridden in deploy.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://district:district@127.0.0.1:55432/district")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_all() -> None:
    """Used for local bring-up and tests. Alembic owns schema changes from here on."""
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with SessionLocal() as session:
        yield session
