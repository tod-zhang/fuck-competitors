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
from sqlmodel import Session, select

from .config import settings
from .db import engine, init_db
from .models import Change, Competitor, Page
from .timeutil import utcnow
from .viewmodels import host_of, url_path

mcp = FastMCP(
    "fuck-competitors",
    instructions=(
        "Competitor-website change monitoring. To answer \"what is competitor X optimizing?\":\n"
        "1) list_competitors() to see who's monitored.\n"
        "2) summarize_window(competitor, days) for a one-shot package, or get_changes()+get_diff() for detail.\n"
        "3) Synthesize by theme — pricing, positioning/messaging, product line, content focus, customers/logos.\n"
        "Weight high-value pages (pricing, homepage, product, customers) heavily. Be cautious: do NOT infer "
        "strategy from trivial or noisy changes, and say when the evidence is thin."
    ),
)

TYPE_LABEL = {"added": "新增", "removed": "删减", "modified": "内容修改", "suspected": "疑似修改"}


def _find_competitor(session: Session, name: str) -> Competitor | None:
    target = (name or "").strip().lower()
    for c in session.exec(select(Competitor)).all():
        if c.name.lower() == target:
            return c
    return None


def _hunks_to_text(hunks: list[dict]) -> str:
    """Render stored diff hunks as a readable unified-diff-style block."""
    lines = []
    for h in hunks or []:
        t, x = h.get("t"), h.get("x", "")
        lines.append({"add": f"+ {x}", "del": f"- {x}", "hunk": f"@@ {x}"}.get(t, f"  {x}"))
    return "\n".join(lines)


@mcp.tool()
def list_competitors() -> list[dict]:
    """List monitored competitors with tracked-page counts and how active they've been (last 7 days)."""
    week_ago = utcnow() - timedelta(days=7)
    with Session(engine) as s:
        out = []
        for c in s.exec(select(Competitor).order_by(Competitor.created_at)).all():
            tracked = len(s.exec(select(Page).where(Page.competitor_id == c.id, Page.status == "active")).all())
            recent = len(s.exec(select(Change).where(Change.competitor_id == c.id, Change.detected_at >= week_ago)).all())
            out.append({
                "name": c.name,
                "host": host_of(c.sitemap_url),
                "tracked_pages": tracked,
                "changes_last_7d": recent,
                "detailed_monitoring": c.detailed_on,
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


class BearerAuth:
    """Minimal ASGI middleware: require `Authorization: Bearer <token>` on HTTP requests."""

    def __init__(self, app, token: str):
        self.app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization", b"").decode() != self._expected:
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
        asgi_app = mcp.streamable_http_app()
        if settings.mcp_token:  # optional shared-secret auth; required when exposed publicly
            asgi_app = BearerAuth(asgi_app, settings.mcp_token)
        uvicorn.run(asgi_app, host=settings.mcp_host, port=settings.mcp_port, log_level="info")
    else:
        mcp.run()  # stdio (local clients launch this as a subprocess)


if __name__ == "__main__":
    main()
