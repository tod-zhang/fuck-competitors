"""Database models (SQLModel). One SQLite file holds everything."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import JSON, Column, Field, SQLModel

from .timeutil import utcnow


class Competitor(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    sitemap_url: str
    color: str = "#39332B"               # sidebar monogram color
    check_interval_hours: int = 24
    detailed_on: bool = False            # opt-in: fetch page content for diffing
    favicon_url: Optional[str] = None    # resolved from the site's <link rel="icon">, if any
    status: str = "ok"                   # ok | error
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class Page(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitor.id", index=True)
    url: str = Field(index=True)
    status: str = "active"               # active | removed
    lastmod: Optional[str] = None        # from sitemap <lastmod>, if present
    is_pinned: bool = False              # deprecated/unused — pinning was removed; detailed monitoring now covers all pages
    latest_content_hash: Optional[str] = None
    etag: Optional[str] = None           # HTTP validators from the last 200, replayed as conditional-GET headers
    last_modified: Optional[str] = None  # so an unchanged page answers 304 instead of re-sending its whole body
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)


class Change(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitor.id", index=True)
    page_id: Optional[int] = Field(default=None, foreign_key="page.id")
    type: str                            # added | removed | modified | suspected
    detected_at: datetime = Field(default_factory=utcnow, index=True)
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_read: bool = False


class Snapshot(SQLModel, table=True):
    """Page content history for detailed monitoring (used to compute content diffs)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="page.id", index=True)
    captured_at: datetime = Field(default_factory=utcnow)
    content_hash: str
    title: Optional[str] = None
    content_text: str = ""
