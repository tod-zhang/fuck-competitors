"""Content feed: the competitor articles to triage each morning, plus the AI features around it.

- ingest(): turn new sitemap pages ("added" changes) into feed items; the first crawl of a
  competitor also seeds the feed with pages whose <lastmod> is recent.
- enrich(): fetch each new article once for title, description, thumbnail and read time (a
  a stored page snapshot, if any, is the fallback), embed it, and match it against my own blog posts.
- sync_my_posts(): fetch + embed my blog (its sitemap) so matching has something to compare to.
- keywords / strategy / suggestions: on-request calls to DataForSEO and the chat model.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

from sqlmodel import Session, select

from . import integrations as ai
from .config import settings
from .db import engine
from .models import AppSetting, Change, Competitor, FeedItem, MyPost, Page, Site, Snapshot, StrategySummary
from .timeutil import utcnow

# Archive / listing URLs that aren't articles.
SKIP_PATH = re.compile(r"/(tag|tags|category|categories|author|page|feed|search|wp-json)(/|$)|\.(xml|pdf|jpe?g|png|gif|webp)$", re.I)
EXCERPT_CHARS = 6000  # title + this much of the body is what gets embedded
MATCH_COUNT = 3
SEED_MAX = 30


# ---------------------------------------------------------------- settings (UI-editable)

def get_setting(session: Session, key: str) -> str:
    row = session.get(AppSetting, key)
    if row is not None:
        return row.value
    return getattr(settings, key, "") or ""


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(AppSetting, key) or AppSetting(key=key)
    row.value = value.strip()
    session.add(row)


def site_key(key: str, site_id: int) -> str:
    """AppSetting key for per-site status text (blog sync, suggestion cache, trends progress)."""
    return f"{key}:{site_id}"


def market(site: Site | None) -> dict:
    """DataForSEO location / language overrides for this site (blank fields fall back to the global ones)."""
    out = {}
    if site and site.location_code.strip().isdigit():
        out["location_code"] = int(site.location_code)
    if site and site.language_code.strip():
        out["language_code"] = site.language_code.strip()
    return out


# ---------------------------------------------------------------- ingest + enrich

def _parse_lastmod(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10])
    except ValueError:
        return None


def _is_article(url: str) -> bool:
    path = urlparse(url).path
    return path not in ("", "/") and not SKIP_PATH.search(path)


def ingest(session: Session, competitor: Competitor) -> int:
    """Create feed items for this competitor's new pages. Returns how many were added."""
    have = set(session.exec(select(FeedItem.page_id).where(FeedItem.competitor_id == competitor.id)).all())
    is_first = not have
    added = 0

    rows = session.exec(
        select(Change, Page)
        .where(Change.competitor_id == competitor.id, Change.type == "added", Change.page_id == Page.id)
    ).all()
    for change, page in rows:
        if page.id in have or not _is_article(page.url):
            continue
        published = _parse_lastmod(page.lastmod) or change.detected_at
        session.add(FeedItem(competitor_id=competitor.id, page_id=page.id, url=page.url,
                             source="new", published_at=published))
        have.add(page.id)
        added += 1

    if is_first:  # seed: recently-modified pages, so the feed isn't empty on day one
        cutoff = utcnow() - timedelta(days=settings.feed_seed_days)
        pages = [p for p in session.exec(
            select(Page).where(Page.competitor_id == competitor.id, Page.status == "active")
        ).all() if _is_article(p.url)]
        recent = sorted(
            ((d, p) for p in pages
             if p.id not in have and (d := _parse_lastmod(p.lastmod)) and d >= cutoff),
            key=lambda dp: dp[0], reverse=True,
        )
        # Sites that stamp every page with "today" make <lastmod> meaningless: if most of the
        # site's articles look freshly modified, don't seed from it at all.
        if not (len(recent) > SEED_MAX and len(recent) > len(pages) / 2):
            for published, page in recent[:SEED_MAX]:
                session.add(FeedItem(competitor_id=competitor.id, page_id=page.id, url=page.url,
                                     source="lastmod", published_at=published))
                added += 1
    session.commit()
    return added


def page_meta(html: bytes, url: str) -> dict:
    """Title, body text, description, thumbnail and read time from a fetched article."""
    from selectolax.parser import HTMLParser

    from .monitor.extract import extract_text

    tree = HTMLParser(html)

    def meta(*names: str) -> str | None:
        for n in names:
            node = tree.css_first(f'meta[property="{n}"]') or tree.css_first(f'meta[name="{n}"]')
            if node and (node.attributes.get("content") or "").strip():
                return node.attributes["content"].strip()
        return None

    title, text = extract_text(html)
    image = meta("og:image", "twitter:image")
    words = len(text.split()) + sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") // 2
    return {
        "title": meta("og:title") or title,
        "text": text,
        "description": meta("og:description", "description", "twitter:description"),
        "image": urljoin(url, image) if image else None,
        "read_minutes": max(1, round(words / 230)) if text else None,
    }


def _fill_text(session: Session, items: list[FeedItem]) -> None:
    """Fetch each new article once for its metadata; fall back to a stored snapshot."""
    from .monitor import fetch

    with fetch.make_client(settings.request_timeout) as client:
        for item in items:
            info = None
            try:
                resp = fetch.polite_get(client, item.url)
                resp.raise_for_status()
                info = page_meta(resp.content, item.url)
            except Exception:  # noqa: BLE001 — an unfetchable page keeps whatever the snapshot has
                snap = session.exec(
                    select(Snapshot).where(Snapshot.page_id == item.page_id).order_by(Snapshot.captured_at.desc())
                ).first()
                if snap is not None:
                    info = {"title": snap.title, "text": snap.content_text, "description": None, "image": None,
                            "read_minutes": max(1, round(len(snap.content_text.split()) / 230))}
            if info:
                item.title, item.description, item.image = info["title"], info["description"], info["image"]
                item.read_minutes = info["read_minutes"]
                item.excerpt = (info["text"] or "")[:EXCERPT_CHARS]
            item.fetched = True
            session.add(item)
            session.commit()


def _embed_text(title: str | None, excerpt: str, url: str) -> str:
    return f"{title or urlparse(url).path}\n\n{excerpt}"[:EXCERPT_CHARS + 500]


def _embed_tag() -> str:
    """Stored with each vector: changing the model or the excerpt length re-embeds on the next run / blog sync."""
    return f"{ai.cfg('embedding_model')}@{EXCERPT_CHARS}"


def _match(item: FeedItem, posts: list[MyPost]) -> list[dict]:
    scored = [
        {"url": p.url, "title": p.title or urlparse(p.url).path, "score": round(ai.cosine(item.embedding, p.embedding), 3)}
        for p in posts
        if p.embedding and p.embed_model == item.embed_model
    ]
    scored.sort(key=lambda m: m["score"], reverse=True)
    return scored[:MATCH_COUNT]


def _embed_and_match(session: Session, items: list[FeedItem]) -> None:
    if not ai.embedding_ready():
        return
    todo = [i for i in items if i.fetched and (i.embedding is None or i.embed_model != _embed_tag())]
    if todo:
        vecs = ai.embed([_embed_text(i.title, i.excerpt, i.url) for i in todo])
        for item, vec in zip(todo, vecs):
            item.embedding, item.embed_model = vec, _embed_tag()
            session.add(item)
        session.commit()
    posts_by_site: dict[int | None, list[MyPost]] = {}
    for p in session.exec(select(MyPost).where(MyPost.embed_model == _embed_tag())).all():
        posts_by_site.setdefault(p.site_id, []).append(p)
    if not posts_by_site:
        return
    site_of = {c.id: c.site_id for c in session.exec(select(Competitor)).all()}
    for item in items:
        posts = posts_by_site.get(site_of.get(item.competitor_id))  # only my posts on the same site
        if item.embedding is not None and posts:
            item.matches = _match(item, posts)
            session.add(item)
    session.commit()


def enrich(session: Session, competitor_id: int | None = None) -> None:
    q = select(FeedItem).where(FeedItem.fetched == False)  # noqa: E712
    if competitor_id is not None:
        q = q.where(FeedItem.competitor_id == competitor_id)
    pending = session.exec(q.order_by(FeedItem.published_at.desc()).limit(settings.feed_enrich_batch)).all()
    _fill_text(session, pending)
    q = select(FeedItem).where(FeedItem.fetched == True, FeedItem.matches == None)  # noqa: E711,E712
    _embed_and_match(session, session.exec(q).all())


def after_check(competitor_id: int) -> None:
    """Hook run after every crawl (scheduler thread)."""
    with Session(engine) as session:
        competitor = session.get(Competitor, competitor_id)
        if competitor is None:
            return
        ingest(session, competitor)
        try:
            while session.exec(select(FeedItem.id).where(
                FeedItem.competitor_id == competitor_id, FeedItem.fetched == False  # noqa: E712
            )).first():
                enrich(session, competitor_id)
        except (ai.NotConfigured, ai.ApiError):
            pass  # embedding is optional; matching just stays empty


def backfill_all() -> None:
    """Startup: feed items for every competitor already in the DB."""
    with Session(engine) as session:
        for competitor in session.exec(select(Competitor)).all():
            ingest(session, competitor)
        try:
            while session.exec(select(FeedItem).where(FeedItem.fetched == False)).first():  # noqa: E712
                enrich(session)
            _embed_and_match(session, session.exec(select(FeedItem).where(FeedItem.matches == None)).all())  # noqa: E711
        except (ai.NotConfigured, ai.ApiError):
            pass  # embedding is optional; titles are already saved per item


# ---------------------------------------------------------------- my blog

def sync_my_posts(site_id: int) -> dict:
    """Fetch this site's blog sitemap, embed every post (capped), then re-match that site's feed."""
    from .monitor import fetch
    from .monitor.extract import extract_text
    from .monitor.sitemap import fetch_all_pages

    with Session(engine) as session:
        site = session.get(Site, site_id)
        sitemap = site.blog_sitemap if site else ""
        if not sitemap:
            return {"error": "先填你自己博客的 sitemap"}
        entries = fetch_all_pages(sitemap, timeout=settings.request_timeout, max_urls=settings.max_sitemap_urls)
        urls = [e.loc for e in entries if _is_article(e.loc)][: settings.my_site_max_pages]
        existing = {p.url: p for p in session.exec(select(MyPost).where(MyPost.site_id == site_id)).all()}

        todo = [u for u in urls if u not in existing or existing[u].embed_model != _embed_tag()]
        texts: list[tuple[str, str, str]] = []
        with fetch.make_client(settings.request_timeout) as client:
            for url in todo:
                try:
                    resp = fetch.polite_get(client, url)
                    resp.raise_for_status()
                    title, text = extract_text(resp.content)
                except Exception:  # noqa: BLE001
                    continue
                texts.append((url, title, text[:EXCERPT_CHARS]))

        vecs = ai.embed([_embed_text(t, x, u) for u, t, x in texts]) if texts else []
        for (url, title, _), vec in zip(texts, vecs):
            post = existing.get(url) or MyPost(url=url, site_id=site_id)
            post.title, post.embedding, post.embed_model, post.synced_at = title, vec, _embed_tag(), utcnow()
            session.add(post)
        for url, post in existing.items():  # posts no longer in my sitemap
            if url not in urls:
                session.delete(post)
        session.commit()

        comp_ids = [c.id for c in session.exec(select(Competitor).where(Competitor.site_id == site_id)).all()]
        _embed_and_match(session, session.exec(select(FeedItem).where(FeedItem.competitor_id.in_(comp_ids))).all())
        return {"posts": len(urls), "embedded": len(vecs)}


# ---------------------------------------------------------------- on-request AI features

KEYWORD_PROMPT = (
    "You pick the SEO target keyword of an article from its title and opening text. Answer with 3 lines: "
    "the main target keyword, then two shorter, broader variants of it (each 1-4 words, lowercase, in the "
    "article's language, no numbering or quotes)."
)


def fetch_keywords(session: Session, item: FeedItem) -> dict:
    comp = session.get(Competitor, item.competitor_id)
    mkt = market(session.get(Site, comp.site_id) if comp and comp.site_id else None)
    ranked = ai.ranked_keywords(item.url, market=mkt)
    seeds: list[str] = []
    if ai.llm_ready():
        try:
            reply = ai.chat(KEYWORD_PROMPT, f"Title: {item.title}\n\n{item.excerpt[:800]}", max_tokens=80)
        except ai.ApiError:
            reply = ""  # fall back to the title below
        seeds = [ln.strip().strip('"-*').strip().lower() for ln in reply.splitlines() if ln.strip()][:3]
    if not seeds and item.title:
        seeds = [item.title.split("|")[0].split(" - ")[0].strip().lower()]
    # Niche long-tail seeds often have no data: fall back to the broader variants.
    seed, seed_row, related = (seeds[0] if seeds else None), None, []
    for candidate in seeds:
        seed_row, related = ai.related_keywords(candidate, market=mkt)
        if seed_row or related:
            seed = candidate
            break
    item.keywords = {"ranked": ranked, "seed": seed, "seed_row": seed_row, "related": related,
                     "fetched_at": utcnow().strftime("%Y-%m-%d %H:%M")}
    session.add(item)
    session.commit()
    return item.keywords


STRATEGY_PROMPT = (
    "你是资深 SEO / 内容营销分析师。下面是一个竞品网站最近一段时间发布的文章（日期 | 标题 | 摘要 | URL）。"
    "分析它的内容策略，只输出 JSON，不要任何别的文字：\n"
    '{"strategy": "一段 3-5 句的中文判断：它在押什么、打法是什么（漏斗位置、意图、节奏，例如某天集中发了多篇）、想抢哪类搜索需求。不确定的地方说明是推断。",'
    ' "themes": [{"name": "主题名（中文，简短）", "description": "1-2 句：这类文章写什么、面向谁", "urls": ["属于这个主题的文章 URL，原样照抄"]}],'
    ' "ideas": ["2-4 个我可以跟进或差异化的选题（中文）"]}\n'
    "themes 给 3-6 个，按文章数从多到少排，每篇文章只归一个主题。"
)
STALE_DAYS = 7


def window_items(session: Session, competitor_id: int, days: int = 30) -> list[FeedItem]:
    cutoff = utcnow() - timedelta(days=days)
    return session.exec(
        select(FeedItem).where(FeedItem.competitor_id == competitor_id, FeedItem.published_at >= cutoff)
        .order_by(FeedItem.published_at.desc())
    ).all()


def latest_summary(session: Session, competitor_id: int) -> StrategySummary | None:
    return session.exec(
        select(StrategySummary).where(StrategySummary.competitor_id == competitor_id)
        .order_by(StrategySummary.created_at.desc())
    ).first()


def is_stale(summary: StrategySummary | None, post_count: int) -> bool:
    """Missing, older than a week, or the competitor published since it was written."""
    if summary is None:
        return True
    return summary.created_at < utcnow() - timedelta(days=STALE_DAYS) or summary.article_count != post_count


def parse_summary(text: str) -> dict:
    """The model's JSON (tolerating ```json fences); older plain-text summaries come back as strategy only."""
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "strategy" in data:
            return {"strategy": data.get("strategy") or "", "themes": data.get("themes") or [],
                    "ideas": data.get("ideas") or []}
    except ValueError:
        pass
    return {"strategy": text or "", "themes": [], "ideas": []}


def summarize_strategy(session: Session, competitor: Competitor, days: int = 30) -> StrategySummary:
    items = window_items(session, competitor.id, days)
    if not items:
        raise ai.ApiError(f"{competitor.name} 最近 {days} 天没有新文章，没东西可总结")
    lines = [f"{i.published_at:%Y-%m-%d} | {i.title or '(无标题)'} | {(i.description or '')[:160]} | {i.url}"
             for i in items[:150]]
    text = ai.chat(STRATEGY_PROMPT, f"竞品：{competitor.name}\n最近 {days} 天共 {len(items)} 篇：\n" + "\n".join(lines),
                   max_tokens=2500)
    summary = StrategySummary(competitor_id=competitor.id, days=days, article_count=len(items), text=text)
    session.add(summary)
    session.commit()
    session.refresh(summary)
    return summary


# Domains that "compete" for everything and are never useful to follow.
GIANTS = {"youtube.com", "wikipedia.org", "en.wikipedia.org", "reddit.com", "www.reddit.com", "amazon.com",
          "www.amazon.com", "facebook.com", "linkedin.com", "pinterest.com", "quora.com", "medium.com",
          "twitter.com", "x.com", "instagram.com", "tiktok.com", "google.com", "alibaba.com", "ebay.com"}


def _bare(host: str) -> str:
    host = (host or "").lower().strip()
    host = re.sub(r"^https?://", "", host).split("/")[0]
    return host[4:] if host.startswith("www.") else host


def suggest_competitors(session: Session, site: Site) -> list[dict]:
    domain = _bare(site.domain)
    if not domain:
        raise ai.ApiError("先在设置里填这个站点的域名")
    tracked = {_bare(urlparse(c.sitemap_url).netloc)
               for c in session.exec(select(Competitor).where(Competitor.site_id == site.id)).all()}
    out = []
    for row in ai.organic_competitors(domain, market=market(site)):
        d = _bare(row["domain"])
        if d == domain or d in GIANTS or row["domain"] in GIANTS:
            continue
        out.append({**row, "domain": d, "tracked": d in tracked})
    return out[:20]


def discover_sitemap(domain: str) -> str | None:
    """robots.txt Sitemap: lines first, then the usual locations. Returns the first one that parses."""
    from .monitor import fetch
    from .monitor.sitemap import parse_sitemap, _maybe_gunzip

    bare = _bare(domain)
    with fetch.make_client(settings.request_timeout) as client:
        for base in (f"https://{bare}", f"https://www.{bare}"):  # some sites only answer on one of them
            candidates: list[str] = []
            try:
                robots = client.get(base + "/robots.txt")
                if robots.status_code < 400:
                    candidates += [ln.split(":", 1)[1].strip() for ln in robots.text.splitlines()
                                   if ln.lower().startswith("sitemap:")]
            except Exception:  # noqa: BLE001 — unreachable host (TLS, DNS): try the other variant
                continue
            candidates += [base + p for p in ("/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml")]
            for url in candidates:
                try:
                    resp = client.get(url)
                    if resp.status_code >= 400:
                        continue
                    if parse_sitemap(_maybe_gunzip(resp.content, url))["entries"]:
                        return url
                except Exception:  # noqa: BLE001
                    continue
    return None
