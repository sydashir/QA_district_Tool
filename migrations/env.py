"""Alembic environment for the product layer.

Two things here are load-bearing:

* **The URL comes from `server.db`, not from `alembic.ini`.** The API, the worker and migrations
  must all point at the same database or a migration silently lands somewhere nobody is reading.
  `server.db.DATABASE_URL` already applies the `DATABASE_URL`-env-var-else-local-docker precedence,
  so it is reused rather than restated.

* **Procrastinate's tables are excluded from autogenerate.** The job queue shares this database and
  owns `procrastinate_*` itself (created by `procrastinate schema --apply`). They are not in our
  `Base.metadata`, so without this filter the first `--autogenerate` would cheerfully emit
  `op.drop_table('procrastinate_jobs')` and destroy the queue — including any audit mid-crawl.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# The repo root, so `import server.models` works even when alembic is invoked from elsewhere.
# `prepend_sys_path = .` in alembic.ini covers the normal case; this covers the rest.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server.db import DATABASE_URL as DEFAULT_DATABASE_URL  # noqa: E402
from server.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables in this database that this migration history does NOT own. See the module docstring.
FOREIGN_TABLE_PREFIXES = ("procrastinate_",)


def get_url() -> str:
    """Env var wins, then whatever `server/db.py` resolved (which is itself env-var-aware)."""
    return os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """Keep autogenerate's reflection blind to tables we do not manage."""
    if type_ == "table" and name is not None:
        return not name.startswith(FOREIGN_TABLE_PREFIXES)
    return True


def include_object(obj, name: str, type_: str, reflected: bool, compare_to) -> bool:
    """Second gate, for objects reached through a table rather than reflected by name."""
    table = getattr(obj, "table", None) if type_ != "table" else obj
    table_name = getattr(table, "name", None)
    if table_name and str(table_name).startswith(FOREIGN_TABLE_PREFIXES):
        return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it — used to hand a DBA a reviewable script."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
