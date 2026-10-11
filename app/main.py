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
from starlette.routing import Route

from . import feed_web, web
from . import mcp_server
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
    async with mcp_server.mcp.session_manager.run():  # the MCP endpoint at /mcp
        yield


app = FastAPI(title="Fuck Competitors", lifespan=lifespan)
if settings.demo_mode:
    @app.middleware("http")
    async def read_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD") and not request.url.path.startswith("/mcp"):  # MCP tools only read
            return PlainTextResponse("演示站是只读的，不能修改数据。", status_code=403)
        return await call_next(request)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
# MCP over HTTP on the app's own address (/mcp), so it works behind the same domain / HTTPS.
# The streamable-HTTP sub-app routes on /mcp itself; its session manager runs in the lifespan above.
app.router.routes.append(Route("/mcp", mcp_server.BearerAuth(mcp_server.mcp.streamable_http_app(), mcp_server.current_token)))
app.include_router(web.router)
app.include_router(feed_web.router)
