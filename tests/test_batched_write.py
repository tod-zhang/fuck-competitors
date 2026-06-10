"""A large baseline crawl must commit in batches, not hold the write lock for the whole
bulk insert — otherwise a concurrent "add competitor" gets stuck and fails with
"database is locked". This verifies the crawl issues multiple commits (deterministic; no timing).
"""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FC_DB_URL"] = f"sqlite:///{_tmp.name}"
os.environ["FC_WRITE_BATCH"] = "100"  # small batch so the test stays small

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import app.service as service  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Competitor, Page  # noqa: E402
from app.monitor.sitemap import PageEntry  # noqa: E402


def test_baseline_commits_in_batches():
    init_db()
    entries = [PageEntry(loc=f"http://x/p{i}") for i in range(450)]
    service.fetch_all_pages = lambda *a, **k: entries  # no network
    service.resolve_favicon = lambda *a, **k: None

    with Session(engine) as s:
        competitor = Competitor(name="big", sitemap_url="http://x/sitemap.xml")
        s.add(competitor)
        s.commit()
        s.refresh(competitor)

        commits = {"n": 0}
        event.listen(s, "after_commit", lambda _s: commits.__setitem__("n", commits["n"] + 1))
        service.run_basic_check(s, competitor)
        n_commits = commits["n"]

    with Session(engine) as s:
        page_count = len(s.exec(select(Page).where(Page.status == "active")).all())

    assert page_count == 450, page_count
    # 450 rows at batch=100 -> at least 4 mid-loop commits + a final one (not one big transaction)
    assert n_commits >= 4, f"expected batched commits, got {n_commits} (write lock held too long)"


if __name__ == "__main__":
    test_baseline_commits_in_batches()
    os.unlink(_tmp.name)
    print("ok: large baseline commits in batches (write lock released frequently)")
