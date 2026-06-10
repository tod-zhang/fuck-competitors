"""SQLite engine + schema bootstrap."""
from __future__ import annotations

import os

from sqlmodel import SQLModel, create_engine

from .config import settings

# Ensure the directory for a file-based SQLite db exists (e.g. ./data/app.db).
if settings.db_url.startswith("sqlite:///"):
    path = settings.db_url.replace("sqlite:///", "", 1)
    if path not in (":memory:", ""):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})


def init_db() -> None:
    # Import models so their tables are registered on SQLModel.metadata before create_all.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Tiny best-effort migrations: add columns that create_all won't add to existing tables."""
    additions = [("competitor", "favicon_url", "VARCHAR")]
    with engine.connect() as conn:
        for table, column, coltype in additions:
            try:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already exists — fine
