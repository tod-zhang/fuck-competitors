"""Sitemaps are untrusted input: parsing must reject entity-expansion / XXE.

Requires defusedxml (a declared dependency). If the stdlib fallback is in effect this fails,
which is the point — production must have the hardened parser.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.monitor.sitemap import parse_sitemap  # noqa: E402

ENTITY_BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">
]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>&lol2;</loc></url>
</urlset>"""


def test_entity_expansion_rejected():
    rejected = False
    try:
        parse_sitemap(ENTITY_BOMB)
    except Exception:
        rejected = True
    assert rejected, "malicious entity-bearing sitemap must be rejected (defusedxml)"


if __name__ == "__main__":
    test_entity_expansion_rejected()
    print("ok: entity-expansion sitemap rejected by the hardened parser")
