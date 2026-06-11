"""Smoke test for the MCP server: it imports, its tools are registered and callable."""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FC_DB_URL"] = f"sqlite:///{_tmp.name}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import mcp_server as m  # noqa: E402


def test_tools_work_on_empty_db():
    init_db()
    assert m.list_competitors() == []
    assert m.get_changes() == []
    assert "error" in m.get_diff(999)
    assert "error" in m.summarize_window("nobody")
    assert "error" in m.get_page_history("/nope")


if __name__ == "__main__":
    test_tools_work_on_empty_db()
    os.unlink(_tmp.name)
    print("ok: MCP server tools registered and callable")
