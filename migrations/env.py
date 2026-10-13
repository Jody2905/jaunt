"""Alembic runs this to apply migrations. It uses the same database URL as the app."""

import os
import sys
from pathlib import Path

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jaunt.db import make_engine  # noqa: E402
from jaunt.models import Base  # noqa: E402

config = context.config
target_metadata = Base.metadata


def database_url():
    return (config.attributes.get("url") or os.environ.get("JAUNT_DATABASE_URL")
            or "sqlite:///jaunt.db")


def run_migrations_offline():
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = make_engine(database_url())
    with engine.connect() as connection:
        # render_as_batch: SQLite can't ALTER most things, so Alembic rebuilds the table instead
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
