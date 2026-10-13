"""Database connection: SQLite by default, PostgreSQL by giving a URL.

On SQLite every transaction starts with BEGIN IMMEDIATE (the write lock is taken
up front), so two travellers can't both read "1 room left" and both book it.
On PostgreSQL the same job is done by locking the inventory row (SELECT ... FOR UPDATE).
"""

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .models import Base


def database_url(arg=None):
    """The database to use: an argument, else $JAUNT_DATABASE_URL, else jaunt.db here.
    A plain file name means a SQLite file."""
    url = arg or os.environ.get("JAUNT_DATABASE_URL") or "jaunt.db"
    return url if "://" in url else f"sqlite:///{url}"


def make_engine(url="sqlite:///jaunt.db"):
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"timeout": 30, "check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_setup(dbapi_conn, _record):
            dbapi_conn.isolation_level = None
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys = ON")
            cur.execute("PRAGMA journal_mode = WAL")
            cur.close()

        @event.listens_for(engine, "begin")
        def _begin_immediate(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        return engine
    return create_engine(url, pool_pre_ping=True)


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def create_tables(engine):
    """For tests and quick demos. Real databases are built with Alembic migrations."""
    Base.metadata.create_all(engine)
