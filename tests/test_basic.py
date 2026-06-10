"""M1 logic tests — stdlib only, no pip install or network needed.

Run:  python3 tests/test_basic.py   (or: pytest)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.monitor.basic import diff_pages  # noqa: E402
from app.monitor.sitemap import parse_sitemap  # noqa: E402

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'

SM_V1 = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset {NS}>
  <url><loc>https://x.com/a</loc><lastmod>2026-06-01</lastmod></url>
  <url><loc>https://x.com/b</loc></url>
  <url><loc>https://x.com/c</loc><lastmod>2026-06-01</lastmod></url>
</urlset>""".encode()

SM_V2 = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset {NS}>
  <url><loc>https://x.com/a</loc><lastmod>2026-06-09</lastmod></url>
  <url><loc>https://x.com/b</loc></url>
  <url><loc>https://x.com/d</loc><lastmod>2026-06-08</lastmod></url>
</urlset>""".encode()

INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex {NS}>
  <sitemap><loc>https://x.com/sitemap-1.xml</loc></sitemap>
  <sitemap><loc>https://x.com/sitemap-2.xml</loc></sitemap>
</sitemapindex>""".encode()


def test_parse_urlset():
    parsed = parse_sitemap(SM_V1)
    assert parsed["type"] == "urlset"
    got = {e.loc: e.lastmod for e in parsed["entries"]}
    assert got == {
        "https://x.com/a": "2026-06-01",
        "https://x.com/b": None,
        "https://x.com/c": "2026-06-01",
    }


def test_parse_index():
    parsed = parse_sitemap(INDEX)
    assert parsed["type"] == "index"
    assert parsed["entries"] == ["https://x.com/sitemap-1.xml", "https://x.com/sitemap-2.xml"]


def test_diff_add_remove_suspected():
    old = {e.loc: e.lastmod for e in parse_sitemap(SM_V1)["entries"]}
    new = parse_sitemap(SM_V2)["entries"]
    changes = {(c.type, c.url) for c in diff_pages(old, new)}

    assert ("added", "https://x.com/d") in changes      # /d is new
    assert ("removed", "https://x.com/c") in changes     # /c dropped from sitemap
    assert ("suspected", "https://x.com/a") in changes   # /a lastmod 06-01 -> 06-09
    # /b has no lastmod and is unchanged -> must NOT produce a suspected change
    assert not any(c.url == "https://x.com/b" for c in diff_pages(old, new))


def test_suspected_carries_lastmod_detail():
    old = {e.loc: e.lastmod for e in parse_sitemap(SM_V1)["entries"]}
    new = parse_sitemap(SM_V2)["entries"]
    a = next(c for c in diff_pages(old, new) if c.url == "https://x.com/a")
    assert a.detail == {"lastmod_from": "2026-06-01", "lastmod_to": "2026-06-09"}


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print("ok: all M1 logic tests passed" if not failures else f"{failures} test(s) failed")
    sys.exit(1 if failures else 0)
