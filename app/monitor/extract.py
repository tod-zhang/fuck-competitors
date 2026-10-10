"""Main-text extraction from an HTML page (used by the content feed for titles and excerpts)."""
from __future__ import annotations

import re


def extract_text(html: bytes) -> tuple[str, str]:
    """Return (title, normalized visible text), stripping nav / footer / script chrome."""
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
