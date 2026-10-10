"""Demo mode: fake data is seeded, nothing is crawled, and every write is rejected."""
import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["FC_DB_URL"] = f"sqlite:///{_tmp.name}"
os.environ["FC_DEMO_MODE"] = "true"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Change, Competitor  # noqa: E402
from app.scheduler import scheduler  # noqa: E402


def test_demo_mode():
    with TestClient(app) as client:
        with Session(engine) as s:
            competitors = s.exec(select(Competitor)).all()
            assert competitors and all(c.sitemap_url.split("/")[2].endswith(".example") for c in competitors)
            change_id = s.exec(select(Change)).first().id
            cid = competitors[0].id

        # nothing crawls: the only job is the daily reseed
        assert [j.id for j in scheduler.get_jobs()] == ["demo-reseed"]

        home = client.get("/")
        assert home.status_code == 200 and "这是演示站" in home.text
        assert client.get(f"/changes/{change_id}").status_code == 200
        assert client.get(f"/competitors/{cid}/settings").status_code == 200

        for path in ["/competitors", f"/competitors/{cid}/detailed", f"/competitors/{cid}/check",
                     f"/competitors/{cid}/update", f"/competitors/{cid}/delete", f"/changes/{change_id}/read"]:
            r = client.post(path, data={"name": "x", "sitemap_url": "https://x.test/sitemap.xml", "interval_hours": "24"},
                            follow_redirects=False)
            assert r.status_code == 403, (path, r.status_code)

        with Session(engine) as s:
            assert len(s.exec(select(Competitor)).all()) == len(competitors)
            assert not s.get(Change, change_id).is_read


if __name__ == "__main__":
    test_demo_mode()
    scheduler.shutdown(wait=False)
    os.unlink(_tmp.name)
    print("ok: demo mode seeds fake data, schedules no crawls, rejects every write")
