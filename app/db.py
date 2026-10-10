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
    _ensure_site()


def _migrate() -> None:
    """Tiny best-effort migrations: add columns that create_all won't add to existing tables."""
    additions = [
        ("competitor", "favicon_url", "VARCHAR"),
        ("page", "etag", "VARCHAR"),
        ("page", "last_modified", "VARCHAR"),
        ("competitor", "site_id", "INTEGER"),
        ("mypost", "site_id", "INTEGER"),
    ]
    with engine.connect() as conn:
        for table, column, coltype in additions:
            try:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already exists — fine


def _ensure_site() -> None:
    """There is always at least one site. Before multi-site, "my site" lived in two settings and every
    competitor / blog post implicitly belonged to it: turn that into the first site and attach them."""
    from sqlmodel import Session, select

    from .models import AppSetting, Competitor, MyPost, Site

    with Session(engine) as s:
        site = s.exec(select(Site).order_by(Site.id)).first()
        if site is None:
            def old(key: str) -> str:
                row = s.get(AppSetting, key)
                return (row.value if row else "") or getattr(settings, key, "")
            domain = old("my_domain").strip()
            site = Site(name=domain or "我的网站", domain=domain, blog_sitemap=old("my_sitemap_url").strip())
            s.add(site)
            s.commit()
            s.refresh(site)
            for key in ("my_sync_status", "suggestions_cache", "trends_status"):  # site-scoped status now
                row = s.get(AppSetting, key)
                if row is not None:
                    s.add(AppSetting(key=f"{key}:{site.id}", value=row.value))
                    s.delete(row)
        for model in (Competitor, MyPost):
            for row in s.exec(select(model).where(model.site_id == None)).all():  # noqa: E711
                row.site_id = site.id
                s.add(row)
        s.commit()
