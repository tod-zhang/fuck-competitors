"""SQLite engine + schema bootstrap."""
from __future__ import annotations

import os

from sqlalchemy import event
from sqlmodel import SQLModel, create_engine

from .config import settings

# Ensure the directory for a file-based SQLite db exists (e.g. ./data/app.db).
if settings.db_url.startswith("sqlite:///"):
    path = settings.db_url.replace("sqlite:///", "", 1)
    if path not in (":memory:", ""):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})


# The scheduler writes from background threads while web requests also write. Without these,
# a web write (e.g. adding a competitor) hits "database is locked" the moment a crawl is mid-write.
# WAL lets reads run alongside the writer; busy_timeout makes a second writer WAIT instead of erroring.
if settings.db_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")   # wait up to 30s for a lock
        cur.execute("PRAGMA synchronous=NORMAL")   # safe + faster under WAL
        cur.close()


def init_db() -> None:
    # Import models so their tables are registered on SQLModel.metadata before create_all.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Tiny best-effort migrations: add columns that create_all won't add to existing tables."""
    additions = [
        ("competitor", "favicon_url", "VARCHAR"),
        ("page", "etag", "VARCHAR"),
        ("page", "last_modified", "VARCHAR"),
    ]
    with engine.connect() as conn:
        for table, column, coltype in additions:
            try:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already exists — fine
