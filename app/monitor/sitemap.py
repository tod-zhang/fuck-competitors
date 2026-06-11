"""Sitemap fetching and parsing.

The parser (`parse_sitemap`) is intentionally dependency-free (stdlib only) so it can be
unit-tested without installing anything. The network fetch (`fetch_all_pages`) imports
httpx lazily for the same reason.

Competitor sitemaps are untrusted input, so XML is parsed with defusedxml to guard against
XXE / billion-laughs / external-entity attacks. (Falls back to stdlib ElementTree only if
defusedxml is unavailable — it is a declared dependency, so production always has it.)
"""
from __future__ import annotations

import gzip
from dataclasses import dataclass

try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:  # pragma: no cover - defusedxml is a declared dependency
    from xml.etree.ElementTree import fromstring as _xml_fromstring


@dataclass
class PageEntry:
    loc: str
    lastmod: str | None = None


def _localname(tag: str) -> str:
    """Strip the XML namespace, e.g. '{...}url' -> 'url'."""
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(data: bytes) -> dict:
    """Parse sitemap XML.

    Returns {"type": "index" | "urlset", "entries": [...]}.
    - urlset  -> entries are PageEntry(loc, lastmod)
    - index   -> entries are child sitemap URLs (str)
    """
    root = _xml_fromstring(data)
    kind = _localname(root.tag)

    if kind == "sitemapindex":
        locs: list[str] = []
        for sitemap in root:
            for child in sitemap:
                if _localname(child.tag) == "loc" and child.text:
                    locs.append(child.text.strip())
        return {"type": "index", "entries": locs}

    entries: list[PageEntry] = []
    for url in root:
        if _localname(url.tag) != "url":
            continue
        loc = lastmod = None
        for child in url:
            name = _localname(child.tag)
            if name == "loc" and child.text:
                loc = child.text.strip()
            elif name == "lastmod" and child.text:
                lastmod = child.text.strip()
        if loc:
            entries.append(PageEntry(loc, lastmod))
    return {"type": "urlset", "entries": entries}


def _maybe_gunzip(content: bytes, url: str) -> bytes:
    if url.endswith(".gz") or content[:2] == b"\x1f\x8b":
        return gzip.decompress(content)
    return content


def fetch_all_pages(
    sitemap_url: str,
    *,
    timeout: int = 20,
    max_urls: int = 50_000,
) -> list[PageEntry]:
    """Fetch a sitemap, following sitemap-index -> child sitemaps, and return all pages."""
    from . import fetch  # lazy: keeps the parser importable without httpx installed

    seen: set[str] = set()
    out: list[PageEntry] = []
    queue = [sitemap_url]

    with fetch.make_client(timeout) as client:
        while queue and len(out) < max_urls:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            resp = fetch.polite_get(client, url)
            resp.raise_for_status()
            parsed = parse_sitemap(_maybe_gunzip(resp.content, url))
            if parsed["type"] == "index":
                queue.extend(parsed["entries"])
            else:
                out.extend(parsed["entries"])

    return out[:max_urls]
