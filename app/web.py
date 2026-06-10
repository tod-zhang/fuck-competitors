"""Server-rendered web UI + form endpoints."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from . import viewmodels as vm
from .db import engine
from .models import Change, Competitor, Page, Snapshot
from .scheduler import check_now, schedule_competitor, unschedule_competitor
from .timeutil import utcnow

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()

PALETTE = ["#111111", "#5E6AD2", "#635BFF", "#0A0A0A", "#F24E1E", "#2563EB", "#059669", "#DB2777"]


def _greet_date(d) -> str:
    return f"{d.year} 年 {d.month} 月 {d.day} 日 · {vm.weekday_cn(d)}"


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    with Session(engine) as s:
        competitors = s.exec(select(Competitor).order_by(Competitor.created_at)).all()
        comp_by_id = {c.id: c for c in competitors}

        changes = s.exec(select(Change).order_by(Change.detected_at.desc()).limit(500)).all()
        page_ids = [ch.page_id for ch in changes if ch.page_id]
        page_by_id = {}
        if page_ids:
            page_by_id = {p.id: p for p in s.exec(select(Page).where(Page.id.in_(page_ids))).all()}

        active_pages = s.exec(select(Page).where(Page.status == "active")).all()
        today = utcnow().date()
        week_ago = utcnow() - timedelta(days=7)
        recent = [ch for ch in changes if ch.detected_at >= week_ago]

        active_by_comp: dict[int, int] = {}
        for p in active_pages:
            active_by_comp[p.competitor_id] = active_by_comp.get(p.competitor_id, 0) + 1
        week_by_comp: dict[int, int] = {}
        for ch in recent:
            week_by_comp[ch.competitor_id] = week_by_comp.get(ch.competitor_id, 0) + 1

        sites = [
            {
                "id": c.id,
                "name": c.name,
                "color": c.color,
                "monogram": vm.monogram(c.name),
                "host": urlparse(c.sitemap_url).netloc or c.sitemap_url,
                "tracked": active_by_comp.get(c.id, 0),
                "week_changes": week_by_comp.get(c.id, 0),
                "freq": vm.interval_label(c.check_interval_hours),
                "detailed": c.detailed_on,
                "favicon": vm.favicon_url_for(c),
            }
            for c in competitors
        ]

        ctx = {
            "competitors": competitors,
            "favicons": {c.id: vm.favicon_url_for(c) for c in competitors},
            "has_data": bool(competitors),
            "greet_date": _greet_date(today),
            "recent_count": len(recent),
            "stats": vm.overview_stats(recent, len(competitors), len(active_pages)),
            "overview_groups": vm.group_changes(changes[:40], comp_by_id, page_by_id, today),
            "timeline_groups": vm.group_changes(changes, comp_by_id, page_by_id, today),
            "sites": sites,
            "nav_changes": len(changes),
        }
        return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/changes/{change_id}", response_class=HTMLResponse)
def change_detail(change_id: int, request: Request):
    with Session(engine) as s:
        change = s.get(Change, change_id)
        if change is None:
            return HTMLResponse("not found", status_code=404)
        page = s.get(Page, change.page_id) if change.page_id else None
        competitor = s.get(Competitor, change.competitor_id)
        detail = vm.build_detail_vm(change, page, competitor)
        return templates.TemplateResponse(request, "partials/drawer.html", detail)


@router.post("/competitors")
def create_competitor(
    name: str = Form(...),
    sitemap_url: str = Form(...),
    interval_hours: int = Form(12),
    detailed: str = Form(None),
):
    with Session(engine) as s:
        count = len(s.exec(select(Competitor)).all())
        competitor = Competitor(
            name=name.strip(),
            sitemap_url=sitemap_url.strip(),
            check_interval_hours=interval_hours,
            detailed_on=bool(detailed),
            color=PALETTE[count % len(PALETTE)],
        )
        s.add(competitor)
        s.commit()
        s.refresh(competitor)
    schedule_competitor(competitor)
    check_now(competitor.id)
    return RedirectResponse("/", status_code=303)


@router.post("/competitors/{competitor_id}/detailed")
def toggle_detailed(competitor_id: int, on: str = Form(None)):
    with Session(engine) as s:
        competitor = s.get(Competitor, competitor_id)
        if competitor:
            competitor.detailed_on = bool(on)
            s.add(competitor)
            s.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/competitors/{competitor_id}/check")
def manual_check(competitor_id: int):
    check_now(competitor_id)
    return RedirectResponse("/", status_code=303)


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
    return RedirectResponse("/", status_code=303)


@router.post("/competitors/{competitor_id}/delete")
def delete_competitor(competitor_id: int):
    with Session(engine) as s:
        page_ids = [p.id for p in s.exec(select(Page).where(Page.competitor_id == competitor_id)).all()]
        for snap in s.exec(select(Snapshot).where(Snapshot.page_id.in_(page_ids))).all() if page_ids else []:
            s.delete(snap)
        for change in s.exec(select(Change).where(Change.competitor_id == competitor_id)).all():
            s.delete(change)
        for page in s.exec(select(Page).where(Page.competitor_id == competitor_id)).all():
            s.delete(page)
        competitor = s.get(Competitor, competitor_id)
        if competitor:
            s.delete(competitor)
        s.commit()
    unschedule_competitor(competitor_id)
    return RedirectResponse("/", status_code=303)


@router.post("/changes/{change_id}/read")
def mark_read(change_id: int):
    with Session(engine) as s:
        change = s.get(Change, change_id)
        if change:
            change.is_read = True
            s.add(change)
            s.commit()
    return RedirectResponse("/", status_code=303)
