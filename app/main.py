"""Application entrypoint: wires DB, scheduler, static files, and the web UI.

Run a SINGLE worker (the scheduler lives in-process):
    uvicorn app.main:app
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import web
from .config import settings
from .db import init_db
from .scheduler import scheduler, start_scheduler

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.demo_mode:
        # Showcase instance: fake data, refreshed daily so "today" stays today. No crawling.
        from .demo import reseed
        reseed()
        scheduler.add_job(reseed, "cron", hour=0, minute=5, id="demo-reseed", replace_existing=True)
        scheduler.start()
    else:
        init_db()
        start_scheduler()
    yield


app = FastAPI(title="Fuck Competitors", lifespan=lifespan)
if settings.demo_mode:
    @app.middleware("http")
    async def read_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD"):
            return PlainTextResponse("演示站是只读的，不能修改数据。", status_code=403)
        return await call_next(request)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(web.router)
