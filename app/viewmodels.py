"""Shape raw DB rows into the structures the templates iterate over."""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from .models import Change, Competitor, Page

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
GLYPH = {"added": "＋", "removed": "－", "modified": "±", "suspected": "±"}
GTYPE = {"added": "add", "removed": "del", "modified": "mod", "suspected": "susp"}


def monogram(name: str) -> str:
    return (name.strip()[:1] or "?").upper()


def interval_label(hours: int) -> str:
    if hours == 24:
        return "每天"
    if hours == 168:
        return "每周"
    return f"每 {hours} 小时"


def url_path(url: str) -> str:
    p = urlparse(url)
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def host_of(url: str) -> str:
    return urlparse(url).netloc


def favicon_url_for(competitor) -> str:
    """Prefer the site's resolved <link rel=icon>; else its /favicon.ico (the UI falls back
    to a letter monogram if even that 404s)."""
    if getattr(competitor, "favicon_url", None):
        return competitor.favicon_url
    host = host_of(competitor.sitemap_url)
    return f"https://{host}/favicon.ico" if host else ""


def weekday_cn(d: date) -> str:
    return WEEKDAYS[d.weekday()]


def day_label(d: date, today: date) -> str:
    if d == today:
        return "今天"
    if d == today - timedelta(days=1):
        return "昨天"
    return f"{d.month} 月 {d.day} 日"


def _note(change: Change) -> str:
    if change.type == "suspected":
        det = change.detail or {}
        return f"lastmod {det.get('lastmod_from') or '—'} → {det.get('lastmod_to') or '—'}，可能已修改"
    if change.type == "modified":
        return "正文内容变化"
    if change.type == "removed":
        return "页面下线"
    return ""


def _dominant(summary: dict) -> str:
    if summary["modified"] or summary["suspected"]:
        return "mod"
    if summary["removed"]:
        return "del"
    return "add"


def _row_vm(change: Change, page: Page | None) -> dict:
    return {
        "id": change.id,
        "type": change.type,
        "gtype": GTYPE[change.type],
        "glyph": GLYPH[change.type],
        "path": url_path(page.url) if page else "—",
        "note": _note(change),
        "suspected": change.type == "suspected",
    }


def group_changes(
    changes: list[Change],
    comp_by_id: dict[int, Competitor],
    page_by_id: dict[int, Page],
    today: date,
) -> list[dict]:
    """changes must be ordered newest-first. Groups by day, then by competitor."""
    days: "OrderedDict[date, OrderedDict[int, list]]" = OrderedDict()
    for ch in changes:
        d = ch.detected_at.date()
        days.setdefault(d, OrderedDict()).setdefault(ch.competitor_id, []).append(ch)

    groups = []
    for d, by_comp in days.items():
        entries = []
        day_total = 0
        for cid, rows in by_comp.items():
            comp = comp_by_id.get(cid)
            summary = {"added": 0, "removed": 0, "modified": 0, "suspected": 0}
            row_vms = []
            for ch in rows:
                summary[ch.type] += 1
                day_total += 1
                row_vms.append(_row_vm(ch, page_by_id.get(ch.page_id)))
            entries.append(
                {
                    "name": comp.name if comp else "—",
                    "monogram": monogram(comp.name) if comp else "?",
                    "color": comp.color if comp else "#39332B",
                    "favicon": favicon_url_for(comp) if comp else "",
                    "summary": summary,
                    "rows": row_vms,
                    "dominant": _dominant(summary),
                    "time": rows[0].detected_at.strftime("%H:%M"),
                }
            )
        groups.append(
            {"label": day_label(d, today), "weekday": weekday_cn(d), "count": day_total, "entries": entries}
        )
    return groups


def overview_stats(recent: list[Change], site_count: int, page_count: int) -> dict:
    added = sum(1 for c in recent if c.type == "added")
    removed = sum(1 for c in recent if c.type == "removed")
    modified = sum(1 for c in recent if c.type in ("modified", "suspected"))
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "sites": site_count,
        "pages": page_count,
        "total": added + removed + modified,
    }


def build_detail_vm(change: Change, page: Page | None, competitor: Competitor | None) -> dict:
    dt = change.detected_at.strftime("%Y/%m/%d %H:%M")
    detail = change.detail or {}
    vm = {
        "change_id": change.id,
        "page_id": page.id if page else None,
        "competitor_id": competitor.id if competitor else None,
        "site": competitor.name if competitor else "—",
        "path": url_path(page.url) if page else "—",
        "full_url": page.url if page else "#",
        "glyph": GLYPH[change.type],
        "gtype": GTYPE[change.type],
        "suspected": change.type == "suspected",
        "mode": "none",
        "meta": [],
    }

    if change.type == "added":
        vm["title"] = "新增页面"
        vm["mode"] = "newpage"
        vm["snapshot"] = {"title": detail.get("title") or "新加入 sitemap 的页面", "url": page.url if page else "#"}
        vm["meta"] = [("检测时间", dt), ("加入监控", page.first_seen_at.strftime("%Y-%m-%d") if page else "—")]
    elif change.type == "removed":
        vm["title"] = "页面已下线"
        vm["mode"] = "gone"
        vm["snapshot"] = {"title": "已从 sitemap 移除", "url": page.url if page else "#"}
        vm["meta"] = [("检测时间", dt), ("状态", "已移除"), ("最后可见", page.last_seen_at.strftime("%Y-%m-%d") if page else "—")]
    elif change.type == "modified":
        vm["title"] = detail.get("title") or "内容已修改"
        vm["mode"] = "diff"
        vm["hunks"] = detail.get("hunks", [])
        vm["meta"] = [("检测时间", dt), ("监控深度", "详细"), ("正文标题", detail.get("title") or "—")]
    elif change.type == "suspected":
        vm["title"] = "页面可能已修改"
        vm["mode"] = "suspected"
        vm["lastmod"] = f"{detail.get('lastmod_from') or '—'} → {detail.get('lastmod_to') or '—'}"
        vm["meta"] = [("检测时间", dt), ("lastmod", vm["lastmod"]), ("监控深度", "基础")]

    return vm
