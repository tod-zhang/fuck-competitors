"""Application entrypoint: wires DB, scheduler, static files, and the web UI.

Run a SINGLE worker (the scheduler lives in-process):
    uvicorn app.main:app
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import web
from .db import init_db
from .scheduler import start_scheduler

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(title="Fuck Competitors", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(web.router)
