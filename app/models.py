"""Database models (SQLModel). One SQLite file holds everything."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import JSON, Column, Field, SQLModel

from .timeutil import utcnow


class Site(SQLModel, table=True):
    """One of my own sites. Competitors, my blog posts and everything derived from them belong to one site."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    domain: str = ""                     # for competitor suggestions
    blog_sitemap: str = ""               # for "closest post on my blog"
    location_code: str = ""              # DataForSEO market; blank = the global default in 设置
    language_code: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Competitor(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    site_id: Optional[int] = Field(default=None, foreign_key="site.id", index=True)
    name: str
    sitemap_url: str
    color: str = "#39332B"               # sidebar monogram color
    check_interval_hours: int = 24
    detailed_on: bool = False            # deprecated/unused — detailed monitoring was removed
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
    """Page content history from the removed detailed monitoring; no longer written (old rows are still read as the feed fallback)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="page.id", index=True)
    captured_at: datetime = Field(default_factory=utcnow)
    content_hash: str
    title: Optional[str] = None
    content_text: str = ""


class FeedItem(SQLModel, table=True):
    """One competitor article in the content feed: something to triage (save / decline) and annotate."""
    id: Optional[int] = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitor.id", index=True)
    page_id: int = Field(foreign_key="page.id", index=True, unique=True)
    url: str
    title: Optional[str] = None
    excerpt: str = ""                    # start of the body text, used for the embedding
    description: Optional[str] = None    # <meta name=description> / og:description
    image: Optional[str] = None          # og:image, shown as the card thumbnail
    read_minutes: Optional[int] = None
    source: str = "new"                  # new (appeared in the sitemap) | lastmod (seeded from a recent <lastmod>)
    published_at: datetime = Field(default_factory=utcnow, index=True)
    status: str = Field(default="new", index=True)   # new | saved | declined
    note: str = ""
    embedding: Optional[list] = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
    embed_model: Optional[str] = None
    matches: Optional[list] = Field(default=None, sa_column=Column(JSON(none_as_null=True)))    # closest posts on my blog
    keywords: Optional[dict] = Field(default=None, sa_column=Column(JSON(none_as_null=True)))   # DataForSEO data, fetched on request
    fetched: bool = False                # title/excerpt fetch attempted
    created_at: datetime = Field(default_factory=utcnow)


class MyPost(SQLModel, table=True):
    """A post on my own blog, embedded so competitor articles can be matched against it."""
    id: Optional[int] = Field(default=None, primary_key=True)
    site_id: Optional[int] = Field(default=None, foreign_key="site.id", index=True)
    url: str = Field(index=True, unique=True)
    title: Optional[str] = None
    embedding: Optional[list] = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
    embed_model: Optional[str] = None
    synced_at: datetime = Field(default_factory=utcnow)


class StrategySummary(SQLModel, table=True):
    """An LLM summary of one competitor's last N days of content."""
    id: Optional[int] = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitor.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    days: int = 30
    article_count: int = 0
    text: str = ""                       # JSON {"strategy": str, "themes": [{name, description, urls}]}


class AppSetting(SQLModel, table=True):
    """Key/value settings edited in the UI (API keys; per-site status text is keyed 'name:site_id')."""
    key: str = Field(primary_key=True)
    value: str = ""
