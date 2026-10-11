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
from .models import Competitor, FeedItem, MyPost, Site
from .scheduler import check_now, scheduler
from .timeutil import utcnow
from .web import create_competitor, current_site, site_competitors, templates

router = APIRouter(prefix="/feed")
STATUSES = ("new", "saved", "declined")
FEED_DAYS = 30  # the feed shows the last month; checked about once a month, so no time filter


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
        "date": f"{item.published_at.year}-{item.published_at.month:02d}-{item.published_at.day:02d}",
        "status": item.status,
        "seeded": item.source == "lastmod",
        "note": item.note,
        "matches": item.matches,
        "keywords": item.keywords,
        "idea": item.idea,
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
def feed_list(request: Request, status: str = "new", comp: int | None = None):
    status = status if status in STATUSES + ("trends",) else "new"
    with Session(engine) as s:
        site = current_site(s, request)
        comps = site_competitors(s, site)
        by_id = {c.id: c for c in comps}
        cutoff = utcnow() - timedelta(days=FEED_DAYS)
        mine = FeedItem.competitor_id.in_(list(by_id))
        base = select(FeedItem).where(mine, FeedItem.published_at >= cutoff)
        count_q = select(FeedItem.status, func.count()).where(mine, FeedItem.published_at >= cutoff)
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
            "comp": comp,
            "counts": counts,
            "groups": list(groups.values()),
            "competitors": [{"id": c.id, **_comp_vm(c)} for c in comps],
            "last_scan": _ago(last_scan),
            "scan_error": any(c.status == "error" for c in comps),
            "my_domain": site.domain,
            "my_sitemap_url": site.blog_sitemap,
            "ready": _ready(),
        }
        if status == "trends":
            ctx.update(_trends_ctx(s, comps, site))
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
        comp = s.get(Competitor, item.competitor_id)
        has_posts = bool(s.exec(select(MyPost.id).where(MyPost.site_id == (comp.site_id if comp else None))).first())
        if item.matches is None and not settings.demo_mode and has_posts:
            try:
                feed._embed_and_match(s, [item])
            except (ai.NotConfigured, ai.ApiError) as exc:
                error = str(exc)
        ctx = {"matches": item.matches, "error": error, "ready": _ready(), "has_posts": has_posts}
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
def refresh_now(request: Request):
    """Re-crawl this site's competitors now (each one's feed hook runs when its check finishes)."""
    with Session(engine) as s:
        ids = [c.id for c in site_competitors(s, current_site(s, request))]
    for cid in ids:
        check_now(cid)
    return {"ok": True, "count": len(ids)}


def _set_status_text(key: str, text: str) -> None:
    with Session(engine) as s:
        feed.set_setting(s, key, text)
        s.commit()


def _sync_job(site_id: int) -> None:
    key = feed.site_key("my_sync_status", site_id)
    _set_status_text(key, "同步中…")
    try:
        r = feed.sync_my_posts(site_id)
        _set_status_text(key, r["error"] if "error" in r else f"已同步 {r['posts']} 篇（新嵌入 {r['embedded']} 篇）")
    except Exception as exc:  # noqa: BLE001
        _set_status_text(key, f"同步失败：{exc}"[:300])


@router.post("/sync-my-blog")
def sync_my_blog(request: Request):
    if not ai.embedding_ready():
        return _err(ai.NotConfigured("还没填嵌入接口的 API Key，到「设置」里填"))
    with Session(engine) as s:
        site_id = current_site(s, request).id
    scheduler.add_job(_sync_job, args=[site_id], id=f"sync-my-blog-{site_id}", replace_existing=True)
    return {"ok": True}


# ---------------------------------------------------------------- competitor suggestions

@router.get("/suggestions", response_class=HTMLResponse)
def suggestions(request: Request, refresh: int = 0):
    """Organic competitors from DataForSEO. Cached in the DB per site, since every call costs money."""
    with Session(engine) as s:
        site = current_site(s, request)
        if settings.demo_mode:  # no paid calls on the demo: show what a real answer looks like
            from .demo import SUGGESTIONS
            rows = [{"domain": d, "intersections": n, "avg_position": pos, "etv": etv}
                    for d, n, pos, etv in SUGGESTIONS.get(feed._bare(site.domain), [])]
            data = {"domain": feed._bare(site.domain), "rows": rows, "demo": True}
            return templates.TemplateResponse(request, "partials/feed_suggestions.html", _hide_followed(s, site, data))
        key = feed.site_key("suggestions_cache", site.id)
        cached = feed.get_setting(s, key)
        data = json.loads(cached) if cached else None
        domain = feed._bare(site.domain)
        if refresh or not data or data.get("domain") != domain:
            try:
                rows = feed.suggest_competitors(s, site)
            except Exception as exc:  # noqa: BLE001
                return _err_html(exc)
            data = {"domain": domain, "rows": rows}
            feed.set_setting(s, key, json.dumps(data))
            s.commit()
        data = _hide_followed(s, site, data)
    return templates.TemplateResponse(request, "partials/feed_suggestions.html", data)


def _ignored(s: Session, site: Site) -> list[str]:
    raw = feed.get_setting(s, feed.site_key("suggestions_ignored", site.id))
    return json.loads(raw) if raw else []


def _hide_followed(s: Session, site: Site, data: dict) -> dict:
    """Leave out competitors this site already follows (also ones followed since the cached query)
    and ones marked 不感兴趣."""
    tracked = {feed._bare(urlparse(c.sitemap_url).netloc) for c in site_competitors(s, site)}
    ignored = set(_ignored(s, site))
    rows = [r for r in data["rows"] if r["domain"] not in tracked and r["domain"] not in ignored]
    hidden_tracked = sum(1 for r in data["rows"] if r["domain"] in tracked)
    return {**data, "rows": rows[:20], "hidden": hidden_tracked, "ignored": len(data["rows"]) - len(rows) - hidden_tracked}


@router.post("/suggestions/ignore")
def ignore_suggestion(request: Request, domain: str = Form(...)):
    """不感兴趣: never suggest this domain again for the current site."""
    with Session(engine) as s:
        site = current_site(s, request)
        ignored = _ignored(s, site)
        if feed._bare(domain) not in ignored:
            ignored.append(feed._bare(domain))
        feed.set_setting(s, feed.site_key("suggestions_ignored", site.id), json.dumps(ignored))
        s.commit()
    return {"ok": True}


@router.post("/suggestions/follow")
def follow(request: Request, domain: str = Form(...)):
    domain = feed._bare(domain)
    sitemap = feed.discover_sitemap(domain)
    if not sitemap:
        return _err(ValueError(f"没找到 {domain} 的 sitemap，可以手动添加"))
    name = domain.split(".")[0].capitalize()
    with Session(engine) as s:
        site_id = current_site(s, request).id
    create_competitor(site_id, name, sitemap)
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


def _trends_ctx(s: Session, comps: list[Competitor], site: Site) -> dict:
    cards = [_trend_card(s, c) for c in comps]
    # stale-with-posts first, then by how much they published
    cards.sort(key=lambda c: (not (c["stale"] and c["posts"]), -c["posts"]))
    return {"cards": cards, "need": sum(1 for c in cards if c["stale"] and c["posts"]),
            "trends_status": feed.get_setting(s, feed.site_key("trends_status", site.id))}


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


def _trends_job(site_id: int, regenerate_all: bool) -> None:
    key = feed.site_key("trends_status", site_id)
    with Session(engine) as s:
        todo = [c for c in s.exec(select(Competitor).where(Competitor.site_id == site_id)).all()
                if (n := len(feed.window_items(s, c.id))) and (regenerate_all or feed.is_stale(feed.latest_summary(s, c.id), n))]
        failed = 0
        for i, c in enumerate(todo, 1):
            _set_status_text(key, f"生成中 {i}/{len(todo)}：{c.name}")
            try:
                feed.summarize_strategy(s, c)
            except Exception:  # noqa: BLE001 — keep going; that card stays stale
                failed += 1
    _set_status_text(key, f"完成 {len(todo) - failed} 个" + (f"，{failed} 个失败" if failed else ""))


@router.post("/trends/generate")
def generate_trends(request: Request, all: int = Form(0)):
    if not ai.llm_ready():
        return _err(ai.NotConfigured("还没填大模型的 API Key，到「设置」里填"))
    with Session(engine) as s:
        site_id = current_site(s, request).id
    _set_status_text(feed.site_key("trends_status", site_id), "排队中…")
    scheduler.add_job(_trends_job, args=[site_id, bool(all)], id=f"trends-{site_id}", replace_existing=True)
    return {"ok": True}


# ---------------------------------------------------------------- settings (API keys + my site)

# The current site's own fields (form name, Site attribute, label, placeholder, hint).
SITE_FIELDS = [
    ("site_name", "name", "站点名称", "例如：宠物用品站", "只在侧边栏切换站点时显示"),
    ("site_domain", "domain", "域名", "example.com", "推荐竞品时用"),
    ("site_blog_sitemap", "blog_sitemap", "博客 sitemap", "https://example.com/post-sitemap.xml", "找「我博客里最接近的文章」时用"),
    ("site_location_code", "location_code", "DataForSEO 国家代码", "留空用默认", "2840 = 美国，2826 = 英国"),
    ("site_language_code", "language_code", "DataForSEO 语言", "留空用默认", "例如 en、de、ja"),
]

CONFIG_SECTIONS = [
    ("DataForSEO", [("dataforseo_login", "账号（邮箱）", "", ""), ("dataforseo_password", "API 密码", "", "在 DataForSEO 后台 API Access 里看"),
                    ("dataforseo_location_code", "默认国家代码", "2840", "站点没单独设置时用；2840 = 美国，2826 = 英国"),
                    ("dataforseo_language_code", "默认语言", "en", "")], "dataforseo"),
    ("嵌入（相似文章）", [("embedding_base_url", "接口地址", "https://openrouter.ai/api/v1", "任意 OpenAI 格式的 /embeddings"),
                     ("embedding_api_key", "API Key", "", ""), ("embedding_model", "模型", "qwen/qwen3-embedding-8b", "换模型后需要重新同步博客")], "embedding"),
    ("大模型（趋势总结）", [("llm_provider", "接口类型", "", ""),
                      ("llm_base_url", "接口地址", "https://api.deepseek.com/v1", "任意 OpenAI 格式的 /chat/completions；Claude 官方 API 不用填"),
                      ("llm_api_key", "API Key", "", ""),
                      ("llm_model", "模型", "deepseek-chat", "OpenAI 兼容接口用非推理模型；Claude 留空或非 claude- 开头时用 claude-opus-5-5")], "llm"),
]


# Settings shown as a dropdown: key -> [(value, label)]
CONFIG_CHOICES = {"llm_provider": [("openai", "OpenAI 兼容（DeepSeek、OpenRouter 等）"), ("anthropic", "Claude 官方 API")]}


def _mask(value: str) -> str:
    return f"已设置 ··· {value[-4:]}" if len(value) > 8 else ("已设置" if value else "")


def config_context(s: Session, site: Site | None) -> dict:
    """Context for partials/feed_config.html; also rendered straight into index.html's #view-settings."""
    sections = []
    for title, fields, service in CONFIG_SECTIONS:
        rows = []
        for key, label, placeholder, hint in fields:
            value = ai.cfg(key) if key in ai.UI_KEYS else feed.get_setting(s, key)
            secret = key in ai.SECRET_KEYS
            rows.append({"key": key, "label": label, "hint": hint, "secret": secret, "choices": CONFIG_CHOICES.get(key),
                         "value": "" if secret else value, "placeholder": _mask(value) if secret else placeholder})
        sections.append({"title": title, "rows": rows, "service": service})
    sites = s.exec(select(Site).order_by(Site.id)).all()
    counts = dict(s.exec(select(Competitor.site_id, func.count()).group_by(Competitor.site_id)).all())
    ctx = {"sections": sections, "site": site,
           "site_list": [{"id": x.id, "name": x.name, "domain": x.domain, "competitors": counts.get(x.id, 0)} for x in sites]}
    if site is not None:  # on the all-sites home there's no current site to edit
        ctx.update({"site_rows": [{"form": f, "label": label, "placeholder": ph, "hint": hint, "value": getattr(site, attr)}
                                  for f, attr, label, ph, hint in SITE_FIELDS],
                    "my_posts": len(s.exec(select(MyPost.id).where(MyPost.site_id == site.id)).all()),
                    "sync_status": feed.get_setting(s, feed.site_key("my_sync_status", site.id))})
    return ctx


@router.get("/config", response_class=HTMLResponse)
def config_form(request: Request):
    with Session(engine) as s:
        ctx = config_context(s, current_site(s, request) if request.query_params.get("site") else None)
    return templates.TemplateResponse(request, "partials/feed_config.html", ctx)


@router.post("/config")
async def config_save(request: Request):
    form = await request.form()
    with Session(engine) as s:
        if "site_name" in form:  # the current-site fields are only on the page inside a site
            site = current_site(s, request)
            for f, attr, *_ in SITE_FIELDS:
                if f in form:
                    setattr(site, attr, form[f].strip())
            site.name = site.name or site.domain or "我的网站"
            s.add(site)
        for _, fields, _ in CONFIG_SECTIONS:
            for key, *_ in fields:
                if form.get(f"clear_{key}"):
                    feed.set_setting(s, key, "")
                elif key in form and (form[key].strip() or key not in ai.SECRET_KEYS):
                    feed.set_setting(s, key, form[key])   # a blank secret field means "keep the saved one"
        s.commit()
    return {"ok": True}


@router.post("/config/test/{service}")
def config_test(service: str):
    try:
        return {"ok": True, "message": ai.test_service(service)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)[:300]}
