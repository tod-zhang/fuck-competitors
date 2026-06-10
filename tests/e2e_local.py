"""End-to-end smoke test for M1, fully local (no external network).

Serves a fake competitor sitemap over 127.0.0.1, runs two checks across a v1->v2 change,
and asserts the full path: httpx fetch -> diff -> SQLite persistence.

Run with the venv:  .venv/bin/python tests/e2e_local.py
"""
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Point the app at a throwaway SQLite file BEFORE importing app modules (engine binds at import).
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FC_DB_URL"] = f"sqlite:///{_tmp.name}"

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import Change, Competitor, Page  # noqa: E402
from app.service import run_check  # noqa: E402

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
V1 = f"""<?xml version="1.0"?><urlset {NS}>
  <url><loc>http://h/a</loc><lastmod>2026-06-01</lastmod></url>
  <url><loc>http://h/b</loc></url>
  <url><loc>http://h/c</loc><lastmod>2026-06-01</lastmod></url>
</urlset>""".encode()
V2 = f"""<?xml version="1.0"?><urlset {NS}>
  <url><loc>http://h/a</loc><lastmod>2026-06-09</lastmod></url>
  <url><loc>http://h/b</loc></url>
  <url><loc>http://h/d</loc><lastmod>2026-06-08</lastmod></url>
</urlset>""".encode()

CURRENT = [V1]  # mutable holder so we can swap the served sitemap between checks


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.end_headers()
        self.wfile.write(CURRENT[0])

    def log_message(self, *args):
        pass  # quiet


def main():
    init_db()
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with Session(engine) as s:
        c = Competitor(name="LocalCo", sitemap_url=f"http://127.0.0.1:{port}/sitemap.xml")
        s.add(c)
        s.commit()
        s.refresh(c)
        cid = c.id

    # First crawl = silent baseline: 3 pages tracked, but NO change records logged.
    r1 = run_check(cid)
    assert r1["baseline"] is True and r1["tracked"] == 3, r1
    assert r1["added"] == 0 and r1["removed"] == 0 and r1["suspected"] == 0, r1

    CURRENT[0] = V2
    r2 = run_check(cid)
    assert r2["baseline"] is False, r2
    assert (r2["added"], r2["removed"], r2["suspected"]) == (1, 1, 1), r2

    with Session(engine) as s:
        active = s.exec(select(Page).where(Page.status == "active")).all()
        removed = s.exec(select(Page).where(Page.status == "removed")).all()
        change_types = sorted(c.type for c in s.exec(select(Change)).all())
    assert sorted(p.url for p in active) == ["http://h/a", "http://h/b", "http://h/d"], active
    assert [p.url for p in removed] == ["http://h/c"], removed
    # baseline logs nothing; only run2's real deltas are recorded: 1 added + 1 removed + 1 suspected
    assert change_types == ["added", "removed", "suspected"], change_types

    server.shutdown()
    os.unlink(_tmp.name)
    print("ok: M1 end-to-end (httpx fetch -> diff -> SQLite) passed")


if __name__ == "__main__":
    main()
