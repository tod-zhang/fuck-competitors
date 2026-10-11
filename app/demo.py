"""Fake demo data for a read-only showcase instance (FC_DEMO_MODE) and for screenshots.

All competitors are fictional and live on the reserved `.example` TLD, so nothing here
points at a real company. Timestamps are relative to "now", so reseeding keeps the
demo looking current. `reseed()` DROPS ALL TABLES — never call it on a real database.
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlmodel import Session, SQLModel, select

from .db import engine, init_db
from .models import Change, Competitor, FeedItem, MyPost, Page, Site, StrategySummary
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
        # two of my own sites: Paylane competes with the second one, everyone else with the first
        main = s.exec(select(Site)).first()
        main.name, main.domain, main.blog_sitemap = "SaaS 工具博客", "mine.example", "https://mine.example/post-sitemap.xml"
        payments = Site(name="支付知识站", domain="pay.mine.example", blog_sitemap="https://pay.mine.example/sitemap.xml",
                        location_code="2826", language_code="en")
        s.add(main); s.add(payments); s.commit(); s.refresh(main); s.refresh(payments)

        def comp(name, color, host, site=main):
            c = Competitor(site_id=site.id, name=name, color=color, sitemap_url=f"https://{host}/sitemap.xml",
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

        nimbus = comp("Nimbus", "#111111", "nimbus.example")
        trackly = comp("Trackly", "#5E6AD2", "trackly.example")
        paylane = comp("Paylane", "#635BFF", "paylane.example", site=payments)
        notewise = comp("Notewise", "#059669", "notewise.example")
        canvasly = comp("Canvasly", "#F24E1E", "canvasly.example")
        for c, n in [(nimbus, 40), (trackly, 18), (paylane, 93), (notewise, 60), (canvasly, 27)]:
            filler(c, n)
        s.commit()

        # Nimbus — today: 3 added
        for path in ["/blog/ship-ai-agents-faster", "/customers/acme-retail", "/templates/ai-chatbot"]:
            change(nimbus, page(nimbus, path), "added", today)

        # Paylane — today: a page went offline
        change(paylane, page(paylane, "/radar/legacy-rules", status="removed"), "removed",
               now.replace(hour=1, minute=40))

        # Notewise — today: suspected (sitemap lastmod moved)
        lastmod_from = (now - timedelta(days=4)).date().isoformat()
        lastmod_to = now.date().isoformat()
        change(notewise, page(notewise, "/pricing", lastmod=lastmod_to), "suspected", now.replace(hour=11, minute=20),
               {"lastmod_from": lastmod_from, "lastmod_to": lastmod_to})

        # Canvasly — yesterday: blog added
        change(canvasly, page(canvasly, "/blog/config-recap"), "added", yday.replace(hour=18, minute=0))

        # Trackly — yesterday: 2 added
        change(trackly, page(trackly, "/changelog/cycles-v2"), "added", yday.replace(hour=22, minute=3))
        change(trackly, page(trackly, "/integrations/slack-v2"), "added", yday.replace(hour=22, minute=3))

        # Older entries so the timeline spans the week
        change(paylane, page(paylane, "/docs/webhooks-v1", status="removed"), "removed", d3.replace(hour=9, minute=10))
        change(notewise, page(notewise, "/ai/meeting-notes"), "added", d5.replace(hour=15, minute=48))
        change(notewise, page(notewise, "/templates/okr-tracker"), "added", d5.replace(hour=15, minute=48))

        s.commit()
        _seed_feed(s, now, page, {"nimbus": nimbus, "trackly": trackly, "paylane": paylane,
                                  "notewise": notewise, "canvasly": canvasly})


# Content feed: fictional articles, my (equally fictional) blog, notes, matches and trend reads.
MY_BLOG = "https://mine.example/blog/"
FEED = [
    # (competitor, path, days ago, read min, title, description, status, note, matches[(slug, score)])
    ("nimbus", "/blog/ai-agents-in-production", 0, 9, "Shipping AI agents to production: 7 lessons from 200 teams",
     "What breaks when agents leave the demo: retries, tool timeouts, eval drift and the observability you need on day one.",
     "new", "", [("ai-agent-observability", 0.86), ("llm-evals-guide", 0.71), ("on-call-for-ml", 0.58)]),
    ("nimbus", "/blog/edge-functions-vs-serverless", 0, 7, "Edge functions vs. serverless: which one should you use in 2026?",
     "A side-by-side on cold starts, pricing and limits, with a decision tree for picking the right runtime.",
     "new", "", [("serverless-cost-calculator", 0.54), ("cold-start-benchmarks", 0.49), ("pricing-page-teardown", 0.31)]),
    ("nimbus", "/customers/acme-retail", 1, 4, "How Acme Retail cut page load time by 63% on Nimbus",
     "Case study: migrating 1,400 storefront pages, image optimisation at the edge and the A/B test that paid for it.",
     "saved", "可以做一篇我们自己的电商客户案例，数据维度照这个来：加载速度 + 转化率。", [("customer-story-shopco", 0.77), ("core-web-vitals-playbook", 0.64), ("image-cdn-guide", 0.6)]),
    ("nimbus", "/blog/preview-environments", 3, 6, "Preview environments for every pull request, explained",
     "Why per-PR previews became table stakes, what they cost and how to keep secrets out of them.",
     "new", "", [("staging-is-dead", 0.81), ("secrets-management", 0.55), ("ci-speed-tips", 0.52)]),
    ("trackly", "/blog/cycles-v2", 1, 5, "Introducing Cycles 2.0: auto-rollover and capacity planning",
     "Unfinished work now rolls over automatically, and team capacity is planned from historical velocity.",
     "new", "", [("sprint-planning-template", 0.72), ("velocity-is-a-lie", 0.66), ("roadmap-vs-backlog", 0.49)]),
    ("trackly", "/blog/linear-vs-jira", 2, 11, "Trackly vs. Jira: an honest comparison for engineering teams",
     "Speed, workflows, pricing and migration effort compared, plus who should stay on Jira.",
     "saved", "对比页是他们的转化主力，我们缺一个「vs Jira」页面，关键词难度不高，优先写。", [("jira-alternatives", 0.88), ("issue-tracker-buyers-guide", 0.7), ("migration-checklist", 0.57)]),
    ("trackly", "/blog/triage-inbox", 4, 4, "Zero-inbox triage for bug reports",
     "A weekly ritual for keeping the bug backlog under 50 issues without a dedicated triage team.",
     "declined", "", [("bug-bash-guide", 0.63), ("support-to-eng-handoff", 0.6), ("on-call-for-ml", 0.38)]),
    ("paylane", "/blog/stablecoin-payouts", 0, 8, "Stablecoin payouts are live in 40 countries",
     "Pay contractors in USDC with same-day settlement; what it means for FX fees and compliance.",
     "new", "", [("cross-border-payouts", 0.52), ("fx-fees-explained", 0.47), ("contractor-payments-guide", 0.41)]),
    ("paylane", "/blog/reduce-checkout-fraud", 2, 10, "How to reduce checkout fraud without hurting conversion",
     "Risk scoring, 3DS step-up rules and the five signals that catch 80% of card testing.",
     "new", "", [("chargeback-guide", 0.75), ("3ds-explained", 0.73), ("checkout-ux-checklist", 0.61)]),
    ("paylane", "/guides/saas-pricing-models", 5, 14, "The complete guide to SaaS pricing models",
     "Per-seat, usage-based, hybrid and credits: when each works, with real examples and billing setups.",
     "new", "", [("usage-based-billing", 0.84), ("pricing-page-teardown", 0.72), ("annual-vs-monthly", 0.66)]),
    ("notewise", "/ai/meeting-notes", 1, 3, "AI meeting notes that actually capture decisions",
     "Notewise now pulls decisions, owners and deadlines out of any call and files them in the right doc.",
     "new", "", [("meeting-notes-template", 0.8), ("async-standups", 0.58), ("decision-log", 0.57)]),
    ("notewise", "/templates/okr-tracker", 2, 3, "Free OKR tracker template (with scoring guide)",
     "A ready-to-copy OKR template with quarterly scoring, check-in prompts and examples for 6 teams.",
     "new", "", [("okr-examples", 0.9), ("goal-setting-framework", 0.71), ("quarterly-planning", 0.62)]),
    ("notewise", "/blog/second-brain", 6, 12, "Building a second brain for your team, not just yourself",
     "How shared knowledge bases fail, and the lightweight structure that keeps a team wiki alive.",
     "new", "", [("team-wiki-guide", 0.76), ("documentation-culture", 0.7), ("decision-log", 0.5)]),
    ("canvasly", "/blog/ai-design-assistant", 0, 6, "Design and build, powered by AI: meet Canvasly Assist",
     "Generate layouts from a prompt, then hand off production-ready components to your codebase.",
     "new", "", [("ai-design-tools", 0.83), ("design-to-code", 0.74), ("design-system-starter", 0.55)]),
    ("canvasly", "/blog/config-recap", 1, 5, "Config 2026 recap: everything we announced",
     "Dev mode for everyone, variables 2.0, and a preview of real-time multiplayer prototyping.",
     "new", "", [("conference-recap-2025", 0.56), ("design-system-starter", 0.48), ("prototyping-guide", 0.46)]),
    ("canvasly", "/templates/pitch-deck", 3, 2, "50 free pitch deck templates for startups",
     "Editable decks used by YC companies, sorted by stage, plus a slide-by-slide checklist.",
     "new", "", [("pitch-deck-guide", 0.87), ("startup-templates", 0.69), ("investor-update-template", 0.6)]),
]
TRENDS = {
    "nimbus": {"strategy": "Nimbus 正在从「托管平台」转向「AI 应用的生产环境」：最近一半的文章都围绕 AI agent 上线后的可靠性和可观测性，"
                           "配合 Edge vs Serverless 这类高搜索量的对比文抢信息型流量，再用客户案例（Acme Retail）收口转化。打法是漏斗上下两头同时做，"
                           "中间的产品教程反而少了（推断）。",
               "themes": [{"name": "AI agent 上线与可靠性", "description": "面向已经在做 agent 的工程团队，讲重试、超时、评测漂移等上线后的问题。",
                           "urls": ["https://nimbus.example/blog/ai-agents-in-production"]},
                          {"name": "运行时选型对比", "description": "Edge、Serverless、预览环境等基础设施选型，抢「X vs Y」类搜索。",
                           "urls": ["https://nimbus.example/blog/edge-functions-vs-serverless", "https://nimbus.example/blog/preview-environments"]},
                          {"name": "客户案例", "description": "带具体数字的迁移案例，服务于底部漏斗。", "urls": ["https://nimbus.example/customers/acme-retail"]}],
               "ideas": ["写一篇「AI agent 上线检查清单」，比他们的经验文更可操作", "做一个 Edge vs Serverless 成本计算器，比文章更有链接价值"]},
    "paylane": {"strategy": "Paylane 在押跨境和定价两条线：稳定币出款是新产品发布，反欺诈和 SaaS 定价模型是长篇指南，明显在抢「怎么做」类高意图搜索。"
                            "指南都超过 10 分钟阅读，说明他们在用深度内容拿外链和排名。",
                "themes": [{"name": "跨境与出款", "description": "稳定币、汇率费用、承包商付款，配合新功能上线。",
                            "urls": ["https://paylane.example/blog/stablecoin-payouts"]},
                           {"name": "长篇操作指南", "description": "反欺诈、定价模型这类 10 分钟以上的深度指南，目标是外链和长尾排名。",
                            "urls": ["https://paylane.example/blog/reduce-checkout-fraud", "https://paylane.example/guides/saas-pricing-models"]}],
                "ideas": ["我们的「usage-based billing」文章和他们的定价指南高度重合（0.84），可以更新并补上案例", "跟进一篇「稳定币出款合规」解读"]},
}


def _seed_feed(s, now, page, comps) -> None:
    slugs = {m[0] for row in FEED for m in row[8]}
    for slug in sorted(slugs):
        s.add(MyPost(site_id=comps["nimbus"].site_id, url=MY_BLOG + slug, title=slug.replace("-", " ").capitalize(), embed_model="demo"))
    for i, (ck, path, ago, mins, title, desc, status, note, matches) in enumerate(FEED):
        c = comps[ck]
        p = page(c, path)
        s.add(FeedItem(
            competitor_id=c.id, page_id=p.id, url=p.url, title=title, description=desc, excerpt=desc,
            read_minutes=mins, published_at=(now - timedelta(days=ago)).replace(hour=(9 + i) % 24),
            status=status, note=note, fetched=True, embed_model="demo",
            matches=[{"url": MY_BLOG + m, "title": m.replace("-", " ").capitalize(), "score": sc} for m, sc in matches],
            keywords=_DEMO_KEYWORDS if path == "/blog/linear-vs-jira" else None,
            idea=IDEAS.get(path),
        ))
    s.commit()
    for ck, data in TRENDS.items():
        c = comps[ck]
        n = sum(1 for row in FEED if row[0] == ck)
        s.add(StrategySummary(competitor_id=c.id, created_at=now - timedelta(hours=3), article_count=n,
                              text=json.dumps(data, ensure_ascii=False)))
    s.commit()


_DEMO_KEYWORDS = {
    "ranked": [{"keyword": "trackly vs jira", "position": 1, "volume": 880, "kd": 12, "intent": "commercial"},
               {"keyword": "jira alternatives", "position": 14, "volume": 6600, "kd": 48, "intent": "commercial"},
               {"keyword": "jira vs linear", "position": 9, "volume": 2400, "kd": 31, "intent": "commercial"}],
    "seed": "jira alternative",
    "seed_row": {"keyword": "jira alternative", "volume": 3600, "kd": 45, "intent": "commercial"},
    "related": [{"keyword": "free jira alternative", "volume": 720, "kd": 29, "intent": "commercial"},
                {"keyword": "jira alternative for small teams", "volume": 320, "kd": 18, "intent": "commercial"},
                {"keyword": "open source jira alternative", "volume": 1300, "kd": 36, "intent": "commercial"}],
    "fetched_at": "演示数据",
}


# 推荐竞品 on the demo site: what DataForSEO's competitors_domain returns, per sample site of mine.
# (domain, keywords both rank for, their average position on those, traffic value of the overlap)
SUGGESTIONS = {
    "mine.example": [("nimbus.example", 1840, 8.2, 42600), ("shipfast.example", 1312, 11.4, 23900),
                     ("trackly.example", 1104, 9.7, 31200), ("deploybox.example", 865, 14.1, 9800),
                     ("canvasly.example", 702, 12.6, 15400), ("logpanda.example", 538, 17.3, 4100),
                     ("tasknest.example", 410, 15.8, 6300)],
    "pay.mine.example": [("paylane.example", 2210, 6.9, 58800), ("ledgerly.example", 1490, 10.2, 21700),
                         ("invoicely.example", 980, 13.5, 12900), ("fraudguard.example", 615, 16.0, 7400)],
}


# AI writing ideas for the sample articles my blog doesn't cover (best match < 0.6), as make_ideas() writes them.
IDEAS = {
    "/blog/edge-functions-vs-serverless": {
        "title": "Edge vs. serverless functions: a cost breakdown at 1M, 10M and 100M requests",
        "angle": "对手只比冷启动和功能；我们按真实流量档位算账，给出每档该选哪个，正好接上我们的成本计算器。",
        "keyword": "edge functions vs serverless"},
    "/blog/stablecoin-payouts": {
        "title": "Paying contractors in USDC: fees, settlement times and the tax paperwork",
        "angle": "对手讲产品功能；我们从付款方的角度写全流程，重点放在合规和报税清单，这块他们没写。",
        "keyword": "pay contractors in usdc"},
    "/blog/config-recap": {
        "title": "Every Config 2026 launch, ranked by how much it changes your design workflow",
        "angle": "对手只罗列发布内容；我们按对日常工作的影响排序，并给每项一句该不该马上用的建议。",
        "keyword": "config 2026 announcements"},
}
