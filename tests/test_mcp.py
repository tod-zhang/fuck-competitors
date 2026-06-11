"""Smoke test for the MCP server: it imports, its tools are registered and callable."""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FC_DB_URL"] = f"sqlite:///{_tmp.name}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio  # noqa: E402

from app.db import init_db  # noqa: E402
from app import mcp_server as m  # noqa: E402


def test_tools_work_on_empty_db():
    init_db()
    assert m.list_competitors() == []
    assert m.get_changes() == []
    assert "error" in m.get_diff(999)
    assert "error" in m.summarize_window("nobody")
    assert "error" in m.get_page_history("/nope")


def test_bearer_auth_blocks_and_allows():
    downstream = {"called": False}

    async def app(scope, receive, send):
        downstream["called"] = True

    async def call(auth: str | None):
        headers = [(b"authorization", auth.encode())] if auth else []
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "http.request"}

        await m.BearerAuth(app, "secret")({"type": "http", "headers": headers}, receive, send)
        return sent

    no_auth = asyncio.run(call(None))
    assert no_auth and no_auth[0]["status"] == 401 and downstream["called"] is False
    asyncio.run(call("Bearer secret"))
    assert downstream["called"] is True


if __name__ == "__main__":
    test_tools_work_on_empty_db()
    test_bearer_auth_blocks_and_allows()
    os.unlink(_tmp.name)
    print("ok: MCP tools callable + bearer auth (401 without token, pass with token)")
