"""Basic (sitemap-only) change detection.

Pure-python, dependency-free, so it can be unit-tested directly. Given the previously
stored pages and a freshly-fetched sitemap, it produces added / removed / suspected records.

'suspected' = a page present in both runs whose <lastmod> changed. It only means "this page
probably changed" — sitemaps never reveal *what* changed. Seeing the actual content diff is
the job of detailed monitoring (see detailed.py, milestone M4).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .sitemap import PageEntry


@dataclass
class ChangeRec:
    type: str  # "added" | "removed" | "suspected"
    url: str
    detail: dict = field(default_factory=dict)


def diff_pages(old: dict[str, str | None], new_entries: list[PageEntry]) -> list[ChangeRec]:
    """Compare stored active pages (url -> lastmod) against freshly-fetched sitemap entries."""
    new_map: dict[str, str | None] = {e.loc: e.lastmod for e in new_entries}
    old_urls, new_urls = set(old), set(new_map)
    changes: list[ChangeRec] = []

    for url in new_urls - old_urls:
        changes.append(ChangeRec("added", url))

    for url in old_urls - new_urls:
        changes.append(ChangeRec("removed", url))

    for url in old_urls & new_urls:
        new_lastmod = new_map[url]
        # Only a *changed, present* lastmod counts as a suspected modification.
        if new_lastmod and new_lastmod != old[url]:
            changes.append(
                ChangeRec("suspected", url, {"lastmod_from": old[url], "lastmod_to": new_lastmod})
            )

    return changes
