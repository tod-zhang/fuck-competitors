"""MCP server exposing the competitor-monitoring data for AI analysis.

Connect it from Claude Code / Codex / Claude Desktop and ask things like
"what has competitor X been optimizing lately?" — the agent pulls the recent changes and
content diffs through these read-only tools and reasons over them.

Run (stdio transport):  python -m app.mcp_server
Reads the same database as the app (FC_DB_URL); WAL means it can read while crawls write.
"""
from __future__ import annotations

from datetime import timedelta

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlmodel import Session, select

from .config import settings
from .db import engine, init_db
from .models import AppSetting, Change, Competitor, FeedItem, Page, Site, StrategySummary
from .timeutil import utcnow
from .viewmodels import host_of, url_path

mcp = FastMCP(
    "fuck-competitors",
    instructions=(
        "Competitors' newly published content, for deciding what to write next. The user runs several sites "
        "of their own; each has its own competitors and blog.\n"
        "1) list_sites() and list_competitors(site) to see what's monitored.\n"
        "2) get_feed(site) for the last month's new articles: each with the closest post on the user's blog "
        "(similarity 0-1; >= 0.8 means they likely covered it already), an AI writing idea when they haven't, "
        "keyword data if fetched, and the user's own notes / saved status.\n"
        "3) get_trends(site) for each competitor's 30-day content strategy read.\n"
        "get_changes / get_page_history / summarize_window cover raw sitemap changes (pages added / removed).\n"
        "Be concrete: name articles and URLs, and say when the evidence is thin."
    ),
)
# Served behind a domain (nginx / Cloudflare) as well as on localhost, and protected by the bearer
# token instead: the default localhost-only Host check would reject every remote client.
mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

TYPE_LABEL = {"added": "新增", "removed": "删减", "modified": "内容修改", "suspected": "疑似修改"}


def _find_competitor(session: Session, name: str) -> Competitor | None:
    target = (name or "").strip().lower()
    for c in session.exec(select(Competitor)).all():
        if c.name.lower() == target:
            return c
    return None


def _find_site(session: Session, site: str | None) -> Site | None:
    """By name or domain (case-insensitive); None = no filter."""
    want = (site or "").strip().lower()
    if not want:
        return None
    for s in session.exec(select(Site)).all():
        if want in (s.name.lower(), s.domain.lower()):
            return s
    raise ValueError(f"site '{site}' not found; call list_sites()")


@mcp.tool()
def list_sites() -> list[dict]:
    """The user's own sites. Competitors, the feed and the blog posts used for matching belong to one site."""
    with Session(engine) as s:
        out = []
        for x in s.exec(select(Site).order_by(Site.id)).all():
            comps = s.exec(select(Competitor).where(Competitor.site_id == x.id)).all()
            out.append({"name": x.name, "domain": x.domain, "blog_sitemap": x.blog_sitemap,
                        "competitors": [c.name for c in comps]})
        return out


@mcp.tool()
def get_feed(site: str | None = None, status: str = "new", competitor: str | None = None,
             days: int = 30, limit: int = 100) -> list[dict]:
    """Competitors' articles published in the last `days`, newest first.

    site: name or domain from list_sites (omit for all). status: "new" (not triaged yet), "saved",
    "declined" or "all". competitor: one competitor's name. Each article has its title, description,
    url, competitor, published date, the closest posts on the user's blog (`similar`, score 0-1),
    the AI writing idea (`idea`: title / angle / keyword) when one was generated, keyword data if
    fetched, and the user's note.
    """
    since = utcnow() - timedelta(days=days)
    with Session(engine) as s:
        try:
            the_site = _find_site(s, site)
        except ValueError as exc:
            return [{"error": str(exc)}]
        comps = {c.id: c for c in s.exec(select(Competitor)).all()
                 if the_site is None or c.site_id == the_site.id}
        if competitor:
            comps = {k: c for k, c in comps.items() if c.name.lower() == competitor.strip().lower()}
            if not comps:
                return [{"error": f"competitor '{competitor}' not found"}]
        q = select(FeedItem).where(FeedItem.competitor_id.in_(list(comps)), FeedItem.published_at >= since)
        if status != "all":
            q = q.where(FeedItem.status == status)
        sites = {x.id: x.name for x in s.exec(select(Site)).all()}
        out = []
        for it in s.exec(q.order_by(FeedItem.published_at.desc()).limit(limit)).all():
            comp = comps[it.competitor_id]
            kw = it.keywords or {}
            out.append({
                "title": it.title, "description": it.description, "url": it.url,
                "competitor": comp.name, "site": sites.get(comp.site_id),
                "published": it.published_at.date().isoformat(), "status": it.status, "note": it.note or None,
                "similar": it.matches or [], "idea": it.idea,
                "keywords": {"ranked": kw.get("ranked"), "seed": kw.get("seed"), "related": kw.get("related")} if kw else None,
            })
        return out


@mcp.tool()
def get_trends(site: str | None = None) -> list[dict]:
    """The latest 30-day content-strategy read per competitor (strategy, recurring themes with their
    article URLs, follow-up ideas), as shown on the 趋势 tab. Competitors never summarised are listed
    with summary null."""
    from .feed import parse_summary

    with Session(engine) as s:
        try:
            the_site = _find_site(s, site)
        except ValueError as exc:
            return [{"error": str(exc)}]
        out = []
        for c in s.exec(select(Competitor).order_by(Competitor.created_at)).all():
            if the_site is not None and c.site_id != the_site.id:
                continue
            summary = s.exec(select(StrategySummary).where(StrategySummary.competitor_id == c.id)
                             .order_by(StrategySummary.created_at.desc())).first()
            out.append({"competitor": c.name, "host": host_of(c.sitemap_url),
                        "summary": parse_summary(summary.text) if summary else None,
                        "articles": summary.article_count if summary else None,
                        "written_at": summary.created_at.isoformat() if summary else None})
        return out


def _hunks_to_text(hunks: list[dict]) -> str:
    """Render stored diff hunks as a readable unified-diff-style block."""
    lines = []
    for h in hunks or []:
        t, x = h.get("t"), h.get("x", "")
        lines.append({"add": f"+ {x}", "del": f"- {x}", "hunk": f"@@ {x}"}.get(t, f"  {x}"))
    return "\n".join(lines)


@mcp.tool()
def list_competitors(site: str | None = None) -> list[dict]:
    """List monitored competitors with tracked-page counts and how active they've been (last 7 days).

    Each competitor belongs to one of my sites ("site" in the output); pass site (name or domain) to filter.
    """
    week_ago = utcnow() - timedelta(days=7)
    with Session(engine) as s:
        sites = {x.id: x for x in s.exec(select(Site)).all()}
        want = (site or "").strip().lower()
        out = []
        for c in s.exec(select(Competitor).order_by(Competitor.created_at)).all():
            mine = sites.get(c.site_id)
            if want and not (mine and want in (mine.name.lower(), mine.domain.lower())):
                continue
            tracked = len(s.exec(select(Page).where(Page.competitor_id == c.id, Page.status == "active")).all())
            recent = len(s.exec(select(Change).where(Change.competitor_id == c.id, Change.detected_at >= week_ago)).all())
            out.append({
                "name": c.name,
                "site": mine.name if mine else None,
                "host": host_of(c.sitemap_url),
                "tracked_pages": tracked,
                "changes_last_7d": recent,
                "status": c.status,
                "last_checked": c.last_checked_at.isoformat() if c.last_checked_at else None,
            })
        return out


@mcp.tool()
def get_changes(competitor: str | None = None, days: int = 14, types: list[str] | None = None, limit: int = 200) -> list[dict]:
    """Recent page changes (added / removed / modified / suspected), newest first.

    competitor: filter to one competitor by name, or omit for all.
    days: look-back window. types: optional filter, e.g. ["modified", "added"].
    For 'modified' rows (has_diff=true), call get_diff(id) to see exactly what text changed.
    """
    since = utcnow() - timedelta(days=days)
    with Session(engine) as s:
        query = select(Change).where(Change.detected_at >= since).order_by(Change.detected_at.desc())
        if competitor:
            comp = _find_competitor(s, competitor)
            if comp is None:
                return [{"error": f"competitor '{competitor}' not found"}]
            query = query.where(Change.competitor_id == comp.id)
        if types:
            query = query.where(Change.type.in_(types))
        changes = s.exec(query.limit(limit)).all()

        comp_by_id = {c.id: c for c in s.exec(select(Competitor)).all()}
        ids = [ch.page_id for ch in changes if ch.page_id]
        page_by_id = {p.id: p for p in s.exec(select(Page).where(Page.id.in_(ids))).all()} if ids else {}

        out = []
        for ch in changes:
            page = page_by_id.get(ch.page_id)
            comp = comp_by_id.get(ch.competitor_id)
            out.append({
                "id": ch.id,
                "competitor": comp.name if comp else None,
                "type": ch.type,
                "type_label": TYPE_LABEL.get(ch.type, ch.type),
                "path": url_path(page.url) if page else None,
                "url": page.url if page else None,
                "detected_at": ch.detected_at.isoformat(),
                "has_diff": ch.type == "modified" and bool((ch.detail or {}).get("hunks")),
            })
        return out


@mcp.tool()
def get_diff(change_id: int) -> dict:
    """Detail of one change. For 'modified', returns the line-by-line content diff (what text was
    added/removed) — the clearest signal of what a competitor changed on a page. For 'suspected',
    returns only the sitemap lastmod change (content wasn't fetched)."""
    with Session(engine) as s:
        ch = s.get(Change, change_id)
        if ch is None:
            return {"error": f"change {change_id} not found"}
        page = s.get(Page, ch.page_id) if ch.page_id else None
        comp = s.get(Competitor, ch.competitor_id)
        detail = ch.detail or {}
        result = {
            "competitor": comp.name if comp else None,
            "type": ch.type,
            "type_label": TYPE_LABEL.get(ch.type, ch.type),
            "url": page.url if page else None,
            "path": url_path(page.url) if page else None,
            "detected_at": ch.detected_at.isoformat(),
        }
        if ch.type == "modified":
            result["title"] = detail.get("title")
            result["diff_text"] = _hunks_to_text(detail.get("hunks", []))
        elif ch.type == "suspected":
            result["lastmod_from"] = detail.get("lastmod_from")
            result["lastmod_to"] = detail.get("lastmod_to")
            result["note"] = "Only the sitemap <lastmod> changed; page content wasn't fetched (basic monitoring)."
        return result


@mcp.tool()
def get_page_history(url_contains: str) -> dict:
    """Every recorded change for pages whose URL contains the given substring (e.g. '/pricing'),
    oldest first — to see how one specific page evolved over time."""
    with Session(engine) as s:
        pages = s.exec(select(Page).where(Page.url.contains(url_contains))).all()
        if not pages:
            return {"error": f"no tracked page matches '{url_contains}'"}
        comp_by_id = {c.id: c for c in s.exec(select(Competitor)).all()}
        matches = []
        for p in pages:
            chs = s.exec(select(Change).where(Change.page_id == p.id).order_by(Change.detected_at)).all()
            comp = comp_by_id.get(p.competitor_id)
            matches.append({
                "url": p.url,
                "competitor": comp.name if comp else None,
                "status": p.status,
                "changes": [{"id": c.id, "type": c.type, "detected_at": c.detected_at.isoformat()} for c in chs],
            })
        return {"matches": matches}


@mcp.tool()
def summarize_window(competitor: str, days: int = 14) -> dict:
    """One-shot package of everything a competitor changed in the last N days — added/removed pages
    plus full content diffs for modified pages — ready to analyze "what are they optimizing?".
    Weight pricing/positioning/product/customer pages; don't over-read trivial changes."""
    since = utcnow() - timedelta(days=days)
    with Session(engine) as s:
        comp = _find_competitor(s, competitor)
        if comp is None:
            return {"error": f"competitor '{competitor}' not found"}
        changes = s.exec(
            select(Change)
            .where(Change.competitor_id == comp.id, Change.detected_at >= since)
            .order_by(Change.detected_at.desc())
        ).all()
        page_by_id = {p.id: p for p in s.exec(select(Page).where(Page.competitor_id == comp.id)).all()}

        bucket: dict[str, list] = {"added": [], "removed": [], "modified": [], "suspected": []}
        for ch in changes:
            page = page_by_id.get(ch.page_id)
            entry = {"path": url_path(page.url) if page else None, "detected_at": ch.detected_at.isoformat()}
            detail = ch.detail or {}
            if ch.type == "modified":
                entry["title"] = detail.get("title")
                entry["diff_text"] = _hunks_to_text(detail.get("hunks", []))
            elif ch.type == "suspected":
                entry["lastmod"] = f"{detail.get('lastmod_from')} → {detail.get('lastmod_to')}"
            bucket.setdefault(ch.type, []).append(entry)

        return {
            "competitor": comp.name,
            "host": host_of(comp.sitemap_url),
            "window_days": days,
            "counts": {k: len(v) for k, v in bucket.items()},
            **bucket,
        }


def current_token() -> str:
    """The token set in 设置 (stored in the DB) wins over FC_MCP_TOKEN; empty = no auth."""
    with Session(engine) as s:
        row = s.get(AppSetting, "mcp_token")
    return (row.value.strip() if row and row.value.strip() else settings.mcp_token).strip()


class BearerAuth:
    """Minimal ASGI middleware: require `Authorization: Bearer <token>` on HTTP requests.
    `token` is a string, or a callable read on each request (so a token set in 设置 applies at once);
    an empty token lets everything through."""

    def __init__(self, app, token):
        self.app = app
        self._token = token

    async def __call__(self, scope, receive, send):
        token = self._token() if callable(self._token) else self._token
        if scope["type"] == "http" and token:
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization", b"").decode() != f"Bearer {token}":
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def main() -> None:
    init_db()  # ensure schema exists (idempotent); reads the app's DB
    if settings.mcp_transport.lower() in ("http", "streamable-http"):
        import uvicorn

        mcp.settings.host = settings.mcp_host
        mcp.settings.port = settings.mcp_port
        asgi_app = BearerAuth(mcp.streamable_http_app(), current_token)  # token: required when exposed publicly
        uvicorn.run(asgi_app, host=settings.mcp_host, port=settings.mcp_port, log_level="info")
    else:
        mcp.run()  # stdio (local clients launch this as a subprocess)


if __name__ == "__main__":
    main()
