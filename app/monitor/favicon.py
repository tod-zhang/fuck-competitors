"""Resolve a site's favicon by parsing its homepage <link rel="icon">.

Returns an absolute icon URL, or None if nothing is declared (the UI then falls back to
/favicon.ico, and finally to a letter monogram).
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

from ..config import settings


def resolve_favicon(site_url: str) -> str | None:
    from selectolax.parser import HTMLParser  # lazy

    from . import fetch  # lazy

    parts = urlparse(site_url)
    if not parts.netloc:
        return None
    base = f"{parts.scheme or 'https'}://{parts.netloc}"

    try:
        with fetch.make_client(settings.request_timeout) as client:
            resp = fetch.polite_get(client, base)
            resp.raise_for_status()
            tree = HTMLParser(resp.content)
    except Exception:
        return None

    best = None
    for node in tree.css("link[rel]"):
        rel = (node.attributes.get("rel") or "").lower()
        href = node.attributes.get("href")
        if not href or "icon" not in rel:
            continue
        url = urljoin(base + "/", href)
        # prefer a plain "icon"/"shortcut icon" over apple-touch-icon, but take anything
        if "apple" not in rel:
            return url
        best = best or url
    return best
