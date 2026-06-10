"""Periodic crawling via APScheduler.

One interval job per competitor, keyed by competitor id. Runs in a background thread
(SQLite is opened with check_same_thread=False). IMPORTANT: run the app with a single
uvicorn worker — multiple workers would each start a scheduler and double-fire jobs.
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from .db import engine
from .models import Competitor
from .service import run_check

scheduler = BackgroundScheduler()


def _job(competitor_id: int) -> None:
    try:
        run_check(competitor_id)
    except Exception:  # noqa: BLE001 — a failing check must not kill the scheduler thread
        pass


def schedule_competitor(competitor: Competitor) -> None:
    scheduler.add_job(
        _job,
        "interval",
        hours=max(1, competitor.check_interval_hours),
        args=[competitor.id],
        id=f"competitor-{competitor.id}",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


def unschedule_competitor(competitor_id: int) -> None:
    try:
        scheduler.remove_job(f"competitor-{competitor_id}")
    except Exception:  # noqa: BLE001
        pass


def check_now(competitor_id: int) -> None:
    """Fire a one-off check immediately (used after add / manual trigger)."""
    scheduler.add_job(_job, args=[competitor_id], id=f"now-{competitor_id}", replace_existing=True)


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()
    with Session(engine) as session:
        for competitor in session.exec(select(Competitor)).all():
            schedule_competitor(competitor)
