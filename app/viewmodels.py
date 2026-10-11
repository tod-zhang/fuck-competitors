"""Shape raw DB rows into the structures the templates iterate over."""
from __future__ import annotations

from urllib.parse import urlparse


def monogram(name: str) -> str:
    return (name.strip()[:1] or "?").upper()


def url_path(url: str) -> str:
    p = urlparse(url)
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def host_of(url: str) -> str:
    return urlparse(url).netloc


def favicon_url_for(competitor) -> str:
    """Prefer the site's resolved <link rel=icon>; else its /favicon.ico (the UI falls back
    to a letter monogram if even that 404s)."""
    if getattr(competitor, "favicon_url", None):
        return competitor.favicon_url
    host = host_of(competitor.sitemap_url)
    return f"https://{host}/favicon.ico" if host else ""
