"""Populate the database with demo data so the UI can be exercised / screenshotted.

Writes to the configured FC_DB_URL (default ./data/app.db). Resets all tables first.
Run:  .venv/bin/python tests/seed_demo.py
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import SQLModel, Session  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import Change, Competitor, Page, Snapshot  # noqa: E402
from app.monitor.diff import make_hunks  # noqa: E402
from app.timeutil import utcnow  # noqa: E402

NOW = utcnow()
TODAY = NOW.replace(hour=2, minute=14)
Yday = NOW - timedelta(days=1)
D7 = NOW - timedelta(days=2)


def reset():
    SQLModel.metadata.drop_all(engine)
    init_db()


def main():
    reset()
    with Session(engine) as s:
        def comp(name, color, host, detailed=False, interval=12):
            c = Competitor(name=name, color=color, sitemap_url=f"https://{host}/sitemap.xml",
                           detailed_on=detailed, check_interval_hours=interval,
                           last_checked_at=NOW, status="ok")
            s.add(c); s.commit(); s.refresh(c)
            return c

        def filler(c, n):
            for i in range(n):
                s.add(Page(competitor_id=c.id, url=f"{c.sitemap_url.rsplit('/',1)[0]}/p/{i}",
                           first_seen_at=D7, last_seen_at=NOW))

        def page(c, path, *, lastmod=None, status="active"):
            url = c.sitemap_url.rsplit("/", 1)[0] + path
            p = Page(competitor_id=c.id, url=url, lastmod=lastmod, status=status,
                     first_seen_at=D7, last_seen_at=NOW)
            s.add(p); s.commit(); s.refresh(p)
            return p

        def change(c, p, type_, when, detail=None):
            s.add(Change(competitor_id=c.id, page_id=p.id, type=type_, detected_at=when, detail=detail or {}))

        vercel = comp("Vercel", "#111111", "vercel.com", detailed=True, interval=6)
        linear = comp("Linear", "#5E6AD2", "linear.app", interval=12)
        stripe = comp("Stripe", "#635BFF", "stripe.com", detailed=True, interval=6)
        notion = comp("Notion", "#0A0A0A", "notion.so", interval=24)
        figma = comp("Figma", "#F24E1E", "figma.com", detailed=True, interval=12)
        for c, n in [(vercel, 40), (linear, 18), (stripe, 93), (notion, 60), (figma, 27)]:
            filler(c, n)
        s.commit()

        # Vercel — today: 3 added + 1 modified (pricing, content diff)
        for path, when in [("/blog/ship-ai-agents-faster", TODAY), ("/customers/openai", TODAY), ("/templates/ai-chatbot", TODAY)]:
            change(vercel, page(vercel, path), "added", when)
        v_pricing = page(vercel, "/pricing")
        old = "Pro 套餐 · $20 / 每位成员 / 月\n无限项目 · 高级分析"
        new = "Pro 套餐 · $25 / 每位成员 / 月\n无限项目 · 高级分析\n联系销售 · 企业版"
        s.add(Snapshot(page_id=v_pricing.id, content_hash="x", title="Pricing – Vercel", content_text=new))
        change(vercel, v_pricing, "modified", TODAY, {"title": "Pricing – Vercel", "hunks": make_hunks(old, new)})

        # Stripe — today: removed
        change(stripe, page(stripe, "/radar/legacy-rules", status="removed"), "removed", NOW.replace(hour=1, minute=40))

        # Notion — today: suspected (lastmod changed)
        change(notion, page(notion, "/pricing", lastmod="2026-06-09"), "suspected", NOW.replace(hour=11, minute=20),
               {"lastmod_from": "2026-06-05", "lastmod_to": "2026-06-09"})

        # Figma — yesterday: hero modified + pricing modified + blog added
        f_home = page(figma, "/")
        change(figma, f_home, "modified", Yday.replace(hour=19, minute=27),
               {"title": "Figma", "hunks": make_hunks("Nothing great is made alone", "Design and build, powered by AI")})
        change(figma, page(figma, "/pricing"), "modified", Yday.replace(hour=19, minute=20), {"title": "Pricing"})
        change(figma, page(figma, "/blog/config-2026-recap"), "added", Yday.replace(hour=18, minute=0))

        # Linear — yesterday: 2 added
        change(linear, page(linear, "/changelog/2026-cycles"), "added", Yday.replace(hour=22, minute=3))
        change(linear, page(linear, "/integrations/slack-v2"), "added", Yday.replace(hour=22, minute=3))

        s.commit()
    print("seeded demo data")


if __name__ == "__main__":
    main()
