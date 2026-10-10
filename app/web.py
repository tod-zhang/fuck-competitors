"""Server-rendered web UI + form endpoints."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select

from . import viewmodels as vm
from .config import settings
from .db import engine
from .models import Change, Competitor, FeedItem, MyPost, Page, Site, Snapshot, StrategySummary
from .scheduler import check_now, schedule_competitor, unschedule_competitor
from .timeutil import utcnow

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["demo"] = settings.demo_mode
router = APIRouter()

PALETTE = ["#111111", "#5E6AD2", "#635BFF", "#0A0A0A", "#F24E1E", "#2563EB", "#059669", "#DB2777"]


def current_site(s: Session, request: Request) -> Site:
    """The site in the URL (?site=, which app.js also adds to every fetch), else the first one.
    init_db guarantees one exists."""
    sid = request.query_params.get("site", "")
    site = s.get(Site, int(sid)) if sid.isdigit() else None
    return site or s.exec(select(Site).order_by(Site.id)).first()


def _back_to(competitor_id: int) -> RedirectResponse:
    """After a competitor form: back to that competitor's site."""
    with Session(engine) as s:
        comp = s.get(Competitor, competitor_id)
    return RedirectResponse(f"/?site={comp.site_id}" if comp and comp.site_id else "/", status_code=303)


def _home(request: Request, s: Session) -> HTMLResponse:
    """/ without ?site=: every site of mine as a card, like fuckseo's /app home."""
    sites = s.exec(select(Site).order_by(Site.id)).all()
    comps = s.exec(select(Competitor)).all()
    site_of = {c.id: c.site_id for c in comps}
    week_ago = utcnow() - timedelta(days=7)
    rows = s.exec(select(FeedItem.competitor_id, FeedItem.status, FeedItem.published_at >= week_ago, func.count())
                  .group_by(FeedItem.competitor_id, FeedItem.status, FeedItem.published_at >= week_ago)).all()
    stats: dict[int, dict] = {x.id: {"competitors": 0, "week": 0, "new": 0, "saved": 0} for x in sites}
    for c in comps:
        if c.site_id in stats:
            stats[c.site_id]["competitors"] += 1
    for comp_id, status, recent, n in rows:
        st = stats.get(site_of.get(comp_id))
        if st is None:
            continue
        st["week"] += n if recent else 0
        if status in ("new", "saved"):
            st[status] += n
    cards = [{"id": x.id, "name": x.name, "domain": x.domain, "monogram": vm.monogram(x.name), **stats[x.id]} for x in sites]
    from .feed_web import config_context  # feed_web imports this module
    ctx = {"home": True, "site": None, "cards": cards, "has_data": True, **config_context(s, None)}
    return templates.TemplateResponse(request, "index.html", ctx)


def site_competitors(s: Session, site: Site) -> list[Competitor]:
    return s.exec(select(Competitor).where(Competitor.site_id == site.id).order_by(Competitor.created_at)).all()


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    with Session(engine) as s:
        if not request.query_params.get("site", "").isdigit() or s.get(Site, int(request.query_params["site"])) is None:
            return _home(request, s)
        site = current_site(s, request)
        competitors = site_competitors(s, site)
        comp_ids = [c.id for c in competitors]
        tracked = dict(s.exec(select(Page.competitor_id, func.count())
                              .where(Page.competitor_id.in_(comp_ids), Page.status == "active")
                              .group_by(Page.competitor_id)).all())
        week_ago = utcnow() - timedelta(days=7)
        new_posts = dict(s.exec(select(FeedItem.competitor_id, func.count())
                                .where(FeedItem.competitor_id.in_(comp_ids), FeedItem.published_at >= week_ago)
                                .group_by(FeedItem.competitor_id)).all())
        sites = [
            {
                "id": c.id,
                "name": c.name,
                "color": c.color,
                "monogram": vm.monogram(c.name),
                "host": urlparse(c.sitemap_url).netloc or c.sitemap_url,
                "tracked": tracked.get(c.id, 0),
                "week_posts": new_posts.get(c.id, 0),
                "freq": vm.interval_label(c.check_interval_hours),
                "favicon": vm.favicon_url_for(c),
            }
            for c in competitors
        ]
        ctx = {
            "site": site,
            "all_sites": s.exec(select(Site).order_by(Site.id)).all(),
            "competitors": competitors,
            "favicons": {c.id: vm.favicon_url_for(c) for c in competitors},
            "has_data": bool(competitors),
            "sites": sites,
        }
        from .feed_web import config_context  # feed_web imports this module
        ctx.update(config_context(s, site))
        return templates.TemplateResponse(request, "index.html", ctx)


@router.post("/competitors")
def add_competitor(
    request: Request,
    name: str = Form(...),
    sitemap_url: str = Form(...),
    interval_hours: int = Form(24),
):
    with Session(engine) as s:
        site_id = current_site(s, request).id
    create_competitor(site_id, name, sitemap_url, interval_hours)
    return RedirectResponse(f"/?site={site_id}", status_code=303)


def create_competitor(site_id: int, name: str, sitemap_url: str, interval_hours: int) -> None:
    with Session(engine) as s:
        count = len(s.exec(select(Competitor)).all())
        competitor = Competitor(
            site_id=site_id,
            name=name.strip(),
            sitemap_url=sitemap_url.strip(),
            check_interval_hours=interval_hours,
            color=PALETTE[count % len(PALETTE)],
        )
        s.add(competitor)
        s.commit()
        s.refresh(competitor)
    schedule_competitor(competitor)
    check_now(competitor.id)


@router.post("/competitors/{competitor_id}/check")
def manual_check(competitor_id: int):
    check_now(competitor_id)
    return _back_to(competitor_id)


@router.get("/competitors/{competitor_id}/settings", response_class=HTMLResponse)
def competitor_settings(competitor_id: int, request: Request):
    with Session(engine) as s:
        competitor = s.get(Competitor, competitor_id)
        if competitor is None:
            return HTMLResponse("not found", status_code=404)
        return templates.TemplateResponse(
            request,
            "partials/settings.html",
            {
                "id": competitor.id,
                "name": competitor.name,
                "sitemap_url": competitor.sitemap_url,
                "interval": competitor.check_interval_hours,
            },
        )


@router.post("/competitors/{competitor_id}/update")
def update_competitor(
    competitor_id: int,
    name: str = Form(...),
    sitemap_url: str = Form(...),
    interval_hours: int = Form(...),
):
    with Session(engine) as s:
        competitor = s.get(Competitor, competitor_id)
        if competitor:
            competitor.name = name.strip()
            competitor.sitemap_url = sitemap_url.strip()
            competitor.check_interval_hours = interval_hours
            s.add(competitor)
            s.commit()
            s.refresh(competitor)
            schedule_competitor(competitor)  # reschedule in case the interval changed
    return _back_to(competitor_id)


@router.post("/competitors/{competitor_id}/delete")
def delete_competitor_route(competitor_id: int):
    back = _back_to(competitor_id)
    delete_competitor(competitor_id)
    return back


def delete_competitor(competitor_id: int) -> None:
    with Session(engine) as s:
        page_ids = [p.id for p in s.exec(select(Page).where(Page.competitor_id == competitor_id)).all()]
        for snap in s.exec(select(Snapshot).where(Snapshot.page_id.in_(page_ids))).all() if page_ids else []:
            s.delete(snap)
        for change in s.exec(select(Change).where(Change.competitor_id == competitor_id)).all():
            s.delete(change)
        for row in s.exec(select(FeedItem).where(FeedItem.competitor_id == competitor_id)).all():
            s.delete(row)
        for row in s.exec(select(StrategySummary).where(StrategySummary.competitor_id == competitor_id)).all():
            s.delete(row)
        for page in s.exec(select(Page).where(Page.competitor_id == competitor_id)).all():
            s.delete(page)
        competitor = s.get(Competitor, competitor_id)
        if competitor:
            s.delete(competitor)
        s.commit()
    unschedule_competitor(competitor_id)


# ---------------------------------------------------------------- my sites

@router.post("/sites")
def add_site(name: str = Form(...), domain: str = Form("")):
    with Session(engine) as s:
        site = Site(name=name.strip() or domain.strip() or "新站点", domain=domain.strip())
        s.add(site)
        s.commit()
        s.refresh(site)
    return {"ok": True, "id": site.id}


@router.post("/sites/{site_id}/delete")
def delete_site(site_id: int):
    """Deletes the site with its competitors (and their history) and its synced blog posts."""
    with Session(engine) as s:
        if len(s.exec(select(Site.id)).all()) <= 1:
            return JSONResponse({"error": "至少要保留一个站点"}, status_code=400)
        comp_ids = [c.id for c in s.exec(select(Competitor).where(Competitor.site_id == site_id)).all()]
    for cid in comp_ids:
        delete_competitor(cid)
    with Session(engine) as s:
        for post in s.exec(select(MyPost).where(MyPost.site_id == site_id)).all():
            s.delete(post)
        site = s.get(Site, site_id)
        if site:
            s.delete(site)
        s.commit()
    return {"ok": True}
