"""Guard the SQLite concurrency fix.

The scheduler writes from background threads while web requests also write. Without WAL +
a busy_timeout, a web write (e.g. adding a competitor) fails with "database is locked" the
moment a crawl is mid-write. This asserts both PRAGMAs are active.
"""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FC_DB_URL"] = f"sqlite:///{_tmp.name}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine  # noqa: E402


def test_wal_and_busy_timeout():
    with engine.connect() as conn:
        journal_mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        busy_timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert str(journal_mode).lower() == "wal", f"journal_mode={journal_mode!r}"
    assert int(busy_timeout) >= 10000, f"busy_timeout={busy_timeout!r}"


if __name__ == "__main__":
    test_wal_and_busy_timeout()
    os.unlink(_tmp.name)
    print("ok: SQLite WAL + busy_timeout active")
