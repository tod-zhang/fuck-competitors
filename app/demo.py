"""Fake demo data for a read-only showcase instance (FC_DEMO_MODE) and for screenshots.

All competitors are fictional and live on the reserved `.example` TLD, so nothing here
points at a real company. Timestamps are relative to "now", so reseeding keeps the
demo looking current. `reseed()` DROPS ALL TABLES — never call it on a real database.
"""
from __future__ import annotations

from datetime import timedelta

from sqlmodel import Session, SQLModel

from .db import engine, init_db
from .models import Change, Competitor, Page, Snapshot
from .monitor.diff import make_hunks
from .timeutil import utcnow


def reseed() -> None:
    SQLModel.metadata.drop_all(engine)
    init_db()

    now = utcnow()
    today = now.replace(hour=2, minute=14)
    yday = now - timedelta(days=1)
    d3 = now - timedelta(days=3)
    d5 = now - timedelta(days=5)
    first_seen = now - timedelta(days=9)

    with Session(engine) as s:
        def comp(name, color, host, detailed=False, interval=24):
            c = Competitor(name=name, color=color, sitemap_url=f"https://{host}/sitemap.xml",
                           detailed_on=detailed, check_interval_hours=interval,
                           last_checked_at=now, status="ok", created_at=first_seen)
            s.add(c); s.commit(); s.refresh(c)
            return c

        def filler(c, n):
            for i in range(n):
                s.add(Page(competitor_id=c.id, url=f"{c.sitemap_url.rsplit('/', 1)[0]}/p/{i}",
                           first_seen_at=first_seen, last_seen_at=now))

        def page(c, path, *, lastmod=None, status="active"):
            url = c.sitemap_url.rsplit("/", 1)[0] + path
            p = Page(competitor_id=c.id, url=url, lastmod=lastmod, status=status,
                     first_seen_at=first_seen, last_seen_at=now)
            s.add(p); s.commit(); s.refresh(p)
            return p

        def change(c, p, type_, when, detail=None):
            s.add(Change(competitor_id=c.id, page_id=p.id, type=type_, detected_at=when, detail=detail or {}))

        def modified(c, path, when, title, old, new):
            p = page(c, path)
            s.add(Snapshot(page_id=p.id, content_hash="demo", title=title, content_text=new))
            change(c, p, "modified", when, {"title": title, "hunks": make_hunks(old, new)})

        nimbus = comp("Nimbus", "#111111", "nimbus.example", detailed=True)
        trackly = comp("Trackly", "#5E6AD2", "trackly.example")
        paylane = comp("Paylane", "#635BFF", "paylane.example", detailed=True)
        notewise = comp("Notewise", "#059669", "notewise.example", interval=168)
        canvasly = comp("Canvasly", "#F24E1E", "canvasly.example", detailed=True)
        for c, n in [(nimbus, 40), (trackly, 18), (paylane, 93), (notewise, 60), (canvasly, 27)]:
            filler(c, n)
        s.commit()

        # Nimbus — today: 3 added + pricing modified (content diff)
        for path in ["/blog/ship-ai-agents-faster", "/customers/acme-retail", "/templates/ai-chatbot"]:
            change(nimbus, page(nimbus, path), "added", today)
        modified(nimbus, "/pricing", today, "Pricing – Nimbus",
                 "Pro 套餐 · $20 / 每位成员 / 月\n无限项目 · 高级分析",
                 "Pro 套餐 · $25 / 每位成员 / 月\n无限项目 · 高级分析\n联系销售 · 企业版")

        # Paylane — today: a page went offline
        change(paylane, page(paylane, "/radar/legacy-rules", status="removed"), "removed",
               now.replace(hour=1, minute=40))

        # Notewise — today: suspected (sitemap lastmod moved)
        lastmod_from = (now - timedelta(days=4)).date().isoformat()
        lastmod_to = now.date().isoformat()
        change(notewise, page(notewise, "/pricing", lastmod=lastmod_to), "suspected", now.replace(hour=11, minute=20),
               {"lastmod_from": lastmod_from, "lastmod_to": lastmod_to})

        # Canvasly — yesterday: homepage hero rewritten + pricing modified + blog added
        modified(canvasly, "/", yday.replace(hour=19, minute=27), "Canvasly",
                 "Nothing great is made alone", "Design and build, powered by AI")
        change(canvasly, page(canvasly, "/pricing"), "modified", yday.replace(hour=19, minute=20), {"title": "Pricing"})
        change(canvasly, page(canvasly, "/blog/config-recap"), "added", yday.replace(hour=18, minute=0))

        # Trackly — yesterday: 2 added
        change(trackly, page(trackly, "/changelog/cycles-v2"), "added", yday.replace(hour=22, minute=3))
        change(trackly, page(trackly, "/integrations/slack-v2"), "added", yday.replace(hour=22, minute=3))

        # Older entries so the timeline spans the week
        modified(paylane, "/pricing", d3.replace(hour=9, minute=12), "Pricing – Paylane",
                 "标准费率 2.9% + 30¢ / 笔\n无月费",
                 "标准费率 2.7% + 30¢ / 笔\n无月费\n大客户可议价")
        change(paylane, page(paylane, "/docs/webhooks-v1", status="removed"), "removed", d3.replace(hour=9, minute=10))
        change(notewise, page(notewise, "/ai/meeting-notes"), "added", d5.replace(hour=15, minute=48))
        change(notewise, page(notewise, "/templates/okr-tracker"), "added", d5.replace(hour=15, minute=48))

        s.commit()
