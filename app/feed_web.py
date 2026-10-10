"""Routes for the content feed view (loaded into index.html's #view-feed on demand)."""
from __future__ import annotations

import html
import json
from collections import OrderedDict
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import Session, func, select

from . import feed
from . import integrations as ai
from . import viewmodels as vm
from .config import settings
from .db import engine
from .models import Competitor, FeedItem, MyPost
from .scheduler import check_now, scheduler
from .timeutil import utcnow
from .web import create_competitor, templates

router = APIRouter(prefix="/feed")
STATUSES = ("new", "saved", "declined")
WINDOWS = (1, 3, 7, 14, 30)


def _comp_vm(c: Competitor | None) -> dict:
    if c is None:
        return {"name": "—", "monogram": "?", "color": "#39332B", "favicon": "", "host": ""}
    return {"name": c.name, "monogram": vm.monogram(c.name), "color": c.color, "favicon": vm.favicon_url_for(c),
            "host": feed._bare(urlparse(c.sitemap_url).netloc)}


def _item_vm(item: FeedItem) -> dict:
    return {
        "id": item.id,
        "title": item.title or vm.url_path(item.url),
        "url": item.url,
        "description": item.description,
        "image": item.image,
        "read_minutes": item.read_minutes,
        "date": f"{item.published_at.year}-{item.published_at.month:02d}-{item.published_at.day:02d}",
        "status": item.status,
        "seeded": item.source == "lastmod",
        "note": item.note,
        "matches": item.matches,
        "keywords": item.keywords,
    }


def _ago(dt) -> str:
    if dt is None:
        return "还没巡检过"
    mins = int((utcnow() - dt).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)} 分钟前"
    if mins < 60 * 24:
        return f"{mins // 60} 小时前"
    return f"{mins // (60 * 24)} 天前"


def _ready() -> dict:
    return {"dfs": ai.dataforseo_ready(), "embed": ai.embedding_ready(), "llm": ai.llm_ready()}


def _err(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=status)


def _err_html(exc: Exception) -> HTMLResponse:
    return HTMLResponse(f'<p class="fd-err">{html.escape(str(exc))}</p>')


@router.get("/list", response_class=HTMLResponse)
def feed_list(request: Request, status: str = "new", window: int = 7, comp: int | None = None):
    status = status if status in STATUSES + ("trends",) else "new"
    window = window if window in WINDOWS else 7
    with Session(engine) as s:
        comps = s.exec(select(Competitor).order_by(Competitor.created_at)).all()
        by_id = {c.id: c for c in comps}
        cutoff = utcnow() - timedelta(days=window)
        base = select(FeedItem).where(FeedItem.published_at >= cutoff)
        count_q = select(FeedItem.status, func.count()).where(FeedItem.published_at >= cutoff)
        if comp:
            base = base.where(FeedItem.competitor_id == comp)
            count_q = count_q.where(FeedItem.competitor_id == comp)
        counts = dict(s.exec(count_q.group_by(FeedItem.status)).all())

        groups: "OrderedDict[int, dict]" = OrderedDict()
        if status != "trends":
            items = s.exec(base.where(FeedItem.status == status)
                           .order_by(FeedItem.published_at.desc(), FeedItem.id.desc()).limit(500)).all()
            for it in items:  # grouped by competitor, the group with the newest post first
                g = groups.setdefault(it.competitor_id, {"id": it.competitor_id, "comp": _comp_vm(by_id.get(it.competitor_id)), "items": []})
                g["items"].append(_item_vm(it))

        last_scan = max((c.last_checked_at for c in comps if c.last_checked_at), default=None)
        ctx = {
            "status": status,
            "window": window,
            "windows": WINDOWS,
            "comp": comp,
            "counts": counts,
            "groups": list(groups.values()),
            "competitors": [{"id": c.id, **_comp_vm(c)} for c in comps],
            "last_scan": _ago(last_scan),
            "scan_error": any(c.status == "error" for c in comps),
            "my_domain": feed.get_setting(s, "my_domain"),
            "my_sitemap_url": feed.get_setting(s, "my_sitemap_url"),
            "my_posts": len(s.exec(select(MyPost.id)).all()),
            "sync_status": feed.get_setting(s, "my_sync_status"),
            "ready": _ready(),
        }
        if status == "trends":
            ctx.update(_trends_ctx(s, comps))
    return templates.TemplateResponse(request, "partials/feed_list.html", ctx)


@router.post("/item/{item_id}/status")
def set_status(item_id: int, status: str = Form(...)):
    if status not in STATUSES:
        return _err(ValueError("bad status"))
    with Session(engine) as s:
        item = s.get(FeedItem, item_id)
        if item:
            item.status = status
            s.add(item)
            s.commit()
    return {"ok": True, "status": status}


@router.post("/dismiss")
def dismiss_many(ids: str = Form(...)):
    """'Dismiss all' for one competitor group: the ids currently shown in it."""
    wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    with Session(engine) as s:
        for item in s.exec(select(FeedItem).where(FeedItem.id.in_(wanted))).all():
            item.status = "declined"
            s.add(item)
        s.commit()
    return {"ok": True, "count": len(wanted)}


@router.post("/item/{item_id}/note")
def set_note(item_id: int, note: str = Form("")):
    with Session(engine) as s:
        item = s.get(FeedItem, item_id)
        if item:
            item.note = note
            s.add(item)
            s.commit()
    return {"ok": True}


@router.get("/item/{item_id}/matches", response_class=HTMLResponse)
def item_matches(item_id: int, request: Request):
    """Closest posts on my blog; computed now if the crawl couldn't (e.g. blog synced afterwards)."""
    with Session(engine) as s:
        item = s.get(FeedItem, item_id)
        if item is None:
            return HTMLResponse("not found", status_code=404)
        error = None
        if item.matches is None and not settings.demo_mode and s.exec(select(MyPost.id)).first():
            try:
                feed._embed_and_match(s, [item])
            except (ai.NotConfigured, ai.ApiError) as exc:
                error = str(exc)
        ctx = {"matches": item.matches, "error": error, "ready": _ready(),
               "has_posts": bool(s.exec(select(MyPost.id)).first())}
    return templates.TemplateResponse(request, "partials/feed_matches.html", ctx)


@router.post("/item/{item_id}/keywords", response_class=HTMLResponse)
def item_keywords(item_id: int, request: Request):
    with Session(engine) as s:
        item = s.get(FeedItem, item_id)
        if item is None:
            return HTMLResponse("not found", status_code=404)
        try:
            kw = feed.fetch_keywords(s, item)
        except Exception as exc:  # noqa: BLE001 — show any API failure in the card
            return _err_html(exc)
    return templates.TemplateResponse(request, "partials/feed_keywords.html", {"keywords": kw})


@router.post("/refresh")
def refresh_now():
    """Re-crawl every competitor now (each one's feed hook runs when its check finishes)."""
    with Session(engine) as s:
        ids = [c.id for c in s.exec(select(Competitor)).all()]
    for cid in ids:
        check_now(cid)
    return {"ok": True, "count": len(ids)}


@router.post("/settings")
def save_settings(my_domain: str = Form(""), my_sitemap_url: str = Form("")):
    with Session(engine) as s:
        feed.set_setting(s, "my_domain", my_domain)
        feed.set_setting(s, "my_sitemap_url", my_sitemap_url)
        s.commit()
    return {"ok": True}


def _set_status_text(key: str, text: str) -> None:
    with Session(engine) as s:
        feed.set_setting(s, key, text)
        s.commit()


def _sync_job() -> None:
    _set_status_text("my_sync_status", "同步中…")
    try:
        r = feed.sync_my_posts()
        _set_status_text("my_sync_status", r["error"] if "error" in r else f"已同步 {r['posts']} 篇（新嵌入 {r['embedded']} 篇）")
    except Exception as exc:  # noqa: BLE001
        _set_status_text("my_sync_status", f"同步失败：{exc}"[:300])


@router.post("/sync-my-blog")
def sync_my_blog():
    if not ai.embedding_ready():
        return _err(ai.NotConfigured("没有配置嵌入接口（FC_EMBEDDING_API_KEY）"))
    scheduler.add_job(_sync_job, id="sync-my-blog", replace_existing=True)
    return {"ok": True}


# ---------------------------------------------------------------- competitor suggestions

@router.get("/suggestions", response_class=HTMLResponse)
def suggestions(request: Request, refresh: int = 0):
    """Organic competitors from DataForSEO. Cached in the DB, since every call costs money."""
    if settings.demo_mode:
        return _err_html(ValueError("演示站不调用付费接口。"))
    with Session(engine) as s:
        cached = feed.get_setting(s, "suggestions_cache")
        data = json.loads(cached) if cached else None
        domain = feed._bare(feed.get_setting(s, "my_domain"))
        if refresh or not data or data.get("domain") != domain:
            try:
                rows = feed.suggest_competitors(s)
            except Exception as exc:  # noqa: BLE001
                return _err_html(exc)
            data = {"domain": domain, "rows": rows}
            feed.set_setting(s, "suggestions_cache", json.dumps(data))
            s.commit()
        tracked = {feed._bare(urlparse(c.sitemap_url).netloc) for c in s.exec(select(Competitor)).all()}
        for row in data["rows"]:
            row["tracked"] = row["domain"] in tracked
    return templates.TemplateResponse(request, "partials/feed_suggestions.html", data)


@router.post("/suggestions/follow")
def follow(domain: str = Form(...)):
    domain = feed._bare(domain)
    sitemap = feed.discover_sitemap(domain)
    if not sitemap:
        return _err(ValueError(f"没找到 {domain} 的 sitemap，可以手动添加"))
    name = domain.split(".")[0].capitalize()
    create_competitor(name=name, sitemap_url=sitemap, interval_hours=settings.default_interval_hours, detailed=None)
    return {"ok": True, "sitemap": sitemap, "name": name}


# ---------------------------------------------------------------- trends (30-day strategy per competitor)

def _trend_card(s: Session, c: Competitor) -> dict:
    posts = feed.window_items(s, c.id)
    summary = feed.latest_summary(s, c.id)
    return {
        "id": c.id,
        "comp": _comp_vm(c),
        "posts": len(posts),
        "stale": feed.is_stale(summary, len(posts)),
        "summary": summary and {**feed.parse_summary(summary.text), "count": summary.article_count,
                                "at": summary.created_at.strftime("%Y-%m-%d %H:%M UTC")},
    }


def _trends_ctx(s: Session, comps: list[Competitor]) -> dict:
    cards = [_trend_card(s, c) for c in comps]
    # stale-with-posts first, then by how much they published
    cards.sort(key=lambda c: (not (c["stale"] and c["posts"]), -c["posts"]))
    return {"cards": cards, "need": sum(1 for c in cards if c["stale"] and c["posts"]),
            "trends_status": feed.get_setting(s, "trends_status")}


@router.post("/strategy/{competitor_id}", response_class=HTMLResponse)
def make_strategy(competitor_id: int, request: Request):
    with Session(engine) as s:
        comp = s.get(Competitor, competitor_id)
        if comp is None:
            return HTMLResponse("not found", status_code=404)
        error = None
        try:
            feed.summarize_strategy(s, comp)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        card = {**_trend_card(s, comp), "error": error}
    return templates.TemplateResponse(request, "partials/feed_trend_card.html", {"c": card, "ready": _ready()})


def _trends_job(regenerate_all: bool) -> None:
    with Session(engine) as s:
        todo = [c for c in s.exec(select(Competitor)).all()
                if (n := len(feed.window_items(s, c.id))) and (regenerate_all or feed.is_stale(feed.latest_summary(s, c.id), n))]
        failed = 0
        for i, c in enumerate(todo, 1):
            _set_status_text("trends_status", f"生成中 {i}/{len(todo)}：{c.name}")
            try:
                feed.summarize_strategy(s, c)
            except Exception:  # noqa: BLE001 — keep going; that card stays stale
                failed += 1
    _set_status_text("trends_status", f"完成 {len(todo) - failed} 个" + (f"，{failed} 个失败" if failed else ""))


@router.post("/trends/generate")
def generate_trends(all: int = Form(0)):
    if not ai.llm_ready():
        return _err(ai.NotConfigured("没有配置总结用的大模型（FC_LLM_API_KEY）"))
    _set_status_text("trends_status", "排队中…")
    scheduler.add_job(_trends_job, args=[bool(all)], id="trends", replace_existing=True)
    return {"ok": True}
