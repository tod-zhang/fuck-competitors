"""Detailed (content-level) monitoring for pinned pages.

Fetches a page, extracts its main visible text, and diffs it against the last snapshot.
A real content change produces a `modified` Change carrying display-ready diff hunks.
The first time a page is seen we just store a baseline snapshot (no change recorded).
"""
from __future__ import annotations

import hashlib
import re

from sqlmodel import Session, select

from ..config import settings
from ..models import Change, Page, Snapshot
from .diff import make_hunks


def extract_text(html: bytes) -> tuple[str, str]:
    """Return (title, normalized visible text), stripping chrome that causes diff noise."""
    from selectolax.parser import HTMLParser  # lazy import

    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else ""

    for selector in ("script", "style", "noscript", "nav", "footer", "svg", "header"):
        for node in tree.css(selector):
            node.decompose()

    root = tree.body or tree.root
    text = root.text(separator="\n") if root else ""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return title, "\n".join(lines)


def check_page_content(session: Session, page: Page, client) -> Change | None:
    """Fetch + diff a single page (via a caller-supplied polite client). Returns the Change if
    content changed, else None. Replays the page's stored ETag / Last-Modified so an unchanged
    page answers 304 and we skip the download entirely."""
    from . import fetch  # lazy import

    conditional: dict[str, str] = {}
    if page.etag:
        conditional["If-None-Match"] = page.etag
    if page.last_modified:
        conditional["If-Modified-Since"] = page.last_modified

    try:
        resp = fetch.polite_get(client, page.url, conditional=conditional or None)
        if resp.status_code == 304:
            return None  # server confirms unchanged — no body fetched, nothing to diff
        resp.raise_for_status()
        html = resp.content
    except Exception:
        return None  # fetch failures (including Blocked) are non-fatal for a single page

    page.etag = resp.headers.get("ETag")
    page.last_modified = resp.headers.get("Last-Modified")

    title, text = extract_text(html)
    new_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    last = session.exec(
        select(Snapshot).where(Snapshot.page_id == page.id).order_by(Snapshot.captured_at.desc())
    ).first()

    page.latest_content_hash = new_hash
    session.add(page)

    if last is None:
        # First capture — establish a baseline, don't report a change.
        session.add(Snapshot(page_id=page.id, content_hash=new_hash, title=title, content_text=text))
        return None

    if last.content_hash == new_hash:
        return None

    change = Change(
        competitor_id=page.competitor_id,
        page_id=page.id,
        type="modified",
        detail={"title": title, "hunks": make_hunks(last.content_text, text)},
    )
    session.add(change)
    session.add(Snapshot(page_id=page.id, content_hash=new_hash, title=title, content_text=text))

    # Prune snapshots beyond the retention window.
    snaps = session.exec(
        select(Snapshot).where(Snapshot.page_id == page.id).order_by(Snapshot.captured_at.desc())
    ).all()
    for stale in snaps[settings.snapshot_retention:]:
        session.delete(stale)

    return change
