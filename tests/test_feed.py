"""Content feed: ingest + lastmod seeding, embedding match, DataForSEO parsing, summary markdown.

No network: httpx.post is replaced with canned responses.
"""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FC_DB_URL"] = f"sqlite:///{_tmp.name}"
os.environ.update(FC_DATAFORSEO_LOGIN="u", FC_DATAFORSEO_PASSWORD="p", FC_EMBEDDING_API_KEY="k",
                  FC_EMBEDDING_PROVIDERS="", FC_LLM_API_KEY="")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta  # noqa: E402

import httpx  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import feed, integrations as ai  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Change, Competitor, FeedItem, MyPost, Page, Site  # noqa: E402
from app.timeutil import utcnow  # noqa: E402


def fake_post(url, json=None, **kw):
    if url.endswith("/embeddings"):
        # 2-d vectors: "seal" texts point one way, everything else the other
        data = [{"index": i, "embedding": [1.0, 0.1] if "seal" in t.lower() else [0.1, 1.0]}
                for i, t in enumerate(json["input"])]
        return httpx.Response(200, json={"data": data}, request=httpx.Request("POST", url))
    if "competitors_domain" in url:
        items = [{"domain": "rival.com", "intersections": 50, "avg_position": 12.34, "metrics": {"organic": {"etv": 99.6, "count": 70}}},
                 {"domain": "www.youtube.com", "intersections": 40, "avg_position": 5, "metrics": {"organic": {}}},
                 {"domain": "mine.com", "intersections": 999, "avg_position": 1, "metrics": {"organic": {}}}]
        return httpx.Response(200, json={"tasks": [{"status_code": 20000, "result": [{"items": items}]}]},
                              request=httpx.Request("POST", url))
    raise AssertionError(url)


def test_ingest_and_seed():
    init_db()
    now = utcnow()
    with Session(engine) as s:
        honest = Competitor(name="Honest", sitemap_url="https://honest.example/sitemap.xml", last_checked_at=now)
        stamper = Competitor(name="Stamper", sitemap_url="https://stamper.example/sitemap.xml", last_checked_at=now)
        s.add(honest); s.add(stamper); s.commit()
        fresh, old = now.date().isoformat(), (now - timedelta(days=90)).date().isoformat()
        for i in range(5):
            s.add(Page(competitor_id=honest.id, url=f"https://honest.example/post-{i}", lastmod=fresh if i < 2 else old))
        s.add(Page(competitor_id=honest.id, url="https://honest.example/tag/x", lastmod=fresh))  # archive: skipped
        for i in range(40):  # every page stamped "today": lastmod is meaningless, seed nothing
            s.add(Page(competitor_id=stamper.id, url=f"https://stamper.example/p-{i}", lastmod=fresh))
        s.commit()
        new_page = Page(competitor_id=stamper.id, url="https://stamper.example/brand-new")
        s.add(new_page); s.commit()
        s.add(Change(competitor_id=stamper.id, page_id=new_page.id, type="added")); s.commit()

        assert feed.ingest(s, honest) == 2
        assert feed.ingest(s, stamper) == 1  # only the real "added" page
        assert feed.ingest(s, honest) == 0   # idempotent
        assert {i.source for i in s.exec(select(FeedItem)).all()} == {"lastmod", "new"}


def test_embed_and_match():
    httpx_post, ai.httpx.post = ai.httpx.post, fake_post
    try:
        with Session(engine) as s:
            s.add(MyPost(url="https://mine.com/seal-guide", title="Seal guide", embedding=[1.0, 0.0], embed_model=feed._embed_tag()))
            s.add(MyPost(url="https://mine.com/pricing", title="Pricing", embedding=[0.0, 1.0], embed_model=feed._embed_tag()))
            s.add(MyPost(url="https://mine.com/old", title="Other model", embedding=[1.0, 0.0], embed_model="other"))
            s.commit()
            item = s.exec(select(FeedItem)).first()
            item.title, item.fetched = "How to pick a pump seal", True
            s.add(item); s.commit()
            feed._embed_and_match(s, [item])
            urls = [m["url"] for m in item.matches]
            assert urls[0] == "https://mine.com/seal-guide" and "https://mine.com/old" not in urls, urls
            assert item.matches[0]["score"] > item.matches[1]["score"]
    finally:
        ai.httpx.post = httpx_post


def test_matches_stay_within_site():
    with Session(engine) as s:
        other = Site(name="Other")
        s.add(other); s.commit()
        item = s.exec(select(FeedItem).where(FeedItem.matches != None)).first()  # noqa: E711
        comp = s.get(Competitor, item.competitor_id)
        comp.site_id, item.matches = other.id, None
        s.add(comp); s.add(item); s.commit()
        feed._embed_and_match(s, [item])   # none of my posts are on this site
        assert item.matches is None
        comp.site_id = None
        s.add(comp); s.commit()


def test_ignored_suggestions():
    from fastapi.testclient import TestClient
    from app.main import app
    with Session(engine) as s:
        sid = s.exec(select(Site)).first().id
        feed.set_setting(s, feed.site_key("suggestions_cache", sid), '{"domain": "", "rows": '
                         '[{"domain": "a.example", "intersections": 5, "avg_position": 3, "etv": 1}, '
                         '{"domain": "b.example", "intersections": 4, "avg_position": 3, "etv": 1}]}')
        s.commit()
    client = TestClient(app)
    assert client.post(f"/feed/suggestions/ignore?site={sid}", data={"domain": "www.a.example"}).json()["ok"]
    page = client.get(f"/feed/suggestions?site={sid}").text
    assert "b.example" in page and "a.example" not in page and "不感兴趣的 1 个" in page


def test_suggestions_filter():
    httpx_post, ai.httpx.post = ai.httpx.post, fake_post
    try:
        with Session(engine) as s:
            site = s.exec(select(Site)).first()
            site.domain = "https://www.mine.com/"
            s.add(site); s.commit()
            rows = feed.suggest_competitors(s, site)
        assert [r["domain"] for r in rows] == ["rival.com"], rows   # giants and myself dropped
        assert rows[0]["avg_position"] == 12.3 and rows[0]["etv"] == 100
    finally:
        ai.httpx.post = httpx_post


def test_unconfigured_llm_and_summary_parsing():
    try:
        ai.chat("s", "u")
        raise AssertionError("expected NotConfigured")
    except ai.NotConfigured:
        pass
    parsed = feed.parse_summary('```json\n{"strategy": "押 GEO", "themes": [{"name": "教程", "urls": ["https://a/1"]}]}\n```')
    assert parsed["strategy"] == "押 GEO" and parsed["themes"][0]["urls"] == ["https://a/1"] and parsed["ideas"] == []
    assert feed.parse_summary("plain text")["strategy"] == "plain text"


def test_claude_provider():
    import anthropic
    from types import SimpleNamespace
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen["client"] = kw
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

        def create(self, **kw):
            seen["request"] = kw
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking"),
                                                                    SimpleNamespace(type="text", text=" OK ")])

    real, anthropic.Anthropic = anthropic.Anthropic, FakeClient
    try:
        with Session(engine) as s:
            feed.set_setting(s, "llm_provider", "anthropic")
            feed.set_setting(s, "llm_api_key", "sk-ant-test")
            s.commit()
        assert ai.chat("sys", "hi") == "OK"
        req = seen["request"]
        # the DeepSeek default model name is replaced by the Claude default; fallbacks on for it
        assert req["model"] == "claude-opus-5-5" and req["system"] == "sys" and req["fallbacks"] == "default"
        assert seen["client"]["api_key"] == "sk-ant-test"
    finally:
        anthropic.Anthropic = real
        with Session(engine) as s:
            feed.set_setting(s, "llm_provider", "")
            feed.set_setting(s, "llm_api_key", "")
            s.commit()


def test_page_meta():
    html = (b'<html><head><title>T | Site</title><meta property="og:image" content="/img/a.png">'
            b'<meta name="description" content="Short summary"></head><body><p>' + b"word " * 460 + b"</p></body></html>")
    meta = feed.page_meta(html, "https://site.example/blog/post")
    assert meta["title"] == "T | Site" and meta["description"] == "Short summary"
    assert meta["image"] == "https://site.example/img/a.png" and meta["read_minutes"] == 2


if __name__ == "__main__":
    test_ingest_and_seed()
    test_embed_and_match()
    test_matches_stay_within_site()
    test_ignored_suggestions()
    test_suggestions_filter()
    test_unconfigured_llm_and_summary_parsing()
    test_claude_provider()
    test_page_meta()
    os.unlink(_tmp.name)
    print("ok: feed ingest/seed, embedding match, competitor suggestions, LLM guard, summary JSON, page metadata")
