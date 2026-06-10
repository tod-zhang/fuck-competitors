<div align="center">

# Fuck Competitors

**Watch your competitors' websites and journal every change.**

A self-hosted, open-source competitor-monitoring app. Point it at a competitor's
`sitemap.xml` and it periodically checks for **added / removed / modified** pages, then
records each change like a diary entry. Warm "手账" (journal) UI, pixel mascot included.

</div>

## Quick start

```bash
docker compose up
# open http://localhost:8000
```

That's it — one container, one SQLite file (persisted in the `fc-data` volume), no external services.

## Getting started

1. **Add a competitor** — click **＋ 添加竞品**, paste its `sitemap.xml`, pick a check interval.
   The first crawl is a **silent baseline**: it records every current page but does *not* flood
   the journal with one "added" entry per page — there's no change signal in the initial inventory.
2. **Let it run** — each competitor is re-crawled on its interval (or hit **↻ 立即巡检** on its card
   to check now). From the second crawl on, only real **新增 / 删减 / 疑似修改** show up in 变更日志.
3. **Watch the pages that matter** — to get line-by-line **content** diffs (pricing, positioning,
   customer logos), open the competitor's card → **「从 N 页中钉选要详细监控的页」**, search by path,
   and pin the key pages. Pinning auto-enables 详细监控 for that competitor; the next detailed crawl
   fetches those pages and records exactly what changed.

## How monitoring works (two tiers)

| Tier | What it does | Cost | Coverage |
| --- | --- | --- | --- |
| **Basic** (always on) | Diffs the sitemap each cycle → page **added / removed**; if a page carries `<lastmod>`, a changed `lastmod` raises a **"suspected modification"** flag. | low | every page |
| **Detailed** (opt-in per competitor) | Fetches the **pinned** pages, extracts main text, and produces a line-by-line **content diff** — catches pricing/positioning/copy changes. | medium | pinned pages |

> Honesty note: basic monitoring can only *suspect* a modification (and only when the site
> provides a truthful `<lastmod>`); it never sees *what* changed. Seeing the actual diff
> requires detailed monitoring. The UI keeps these distinct on purpose.

Pin the high-value pages (pricing, homepage, customers) from the competitor card's page browser —
those are the B2B signals worth a line-by-line diff, and pinning turns on **详细监控** automatically.

## Configuration

All settings are env vars prefixed `FC_` (see `.env.example`):

| Var | Default | Meaning |
| --- | --- | --- |
| `FC_DB_URL` | `sqlite:///./data/app.db` | database location |
| `FC_DEFAULT_INTERVAL_HOURS` | `12` | default crawl interval |
| `FC_REQUEST_TIMEOUT` | `20` | per-request timeout (s) |
| `FC_MAX_SITEMAP_URLS` | `50000` | cap per sitemap |
| `FC_SNAPSHOT_RETENTION` | `10` | content snapshots kept per pinned page |

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload        # http://localhost:8000

python tests/test_basic.py           # sitemap-parse + diff (stdlib only)
python tests/e2e_local.py            # full fetch→diff→persist over a local server
python tests/seed_demo.py            # load demo data to explore the UI
```

## Architecture

- **FastAPI** app, server-rendered **Jinja2** templates (no build step), vanilla JS for the change-detail drawer.
- **APScheduler** runs one in-process interval job per competitor → **run a single worker**.
- **SQLite** via SQLModel: `competitors / pages / changes / snapshots`.
- `app/monitor/` holds the dependency-free core: `sitemap.py` (fetch + parse), `basic.py` (add/remove/suspected diff), `detailed.py` (content fetch + extract), `diff.py` (line diff).

```
app/
├── main.py          # FastAPI app + lifespan (starts scheduler)
├── web.py           # web routes + form endpoints
├── service.py       # check orchestration (basic + detailed)
├── scheduler.py     # APScheduler jobs
├── models.py        # SQLModel tables
├── viewmodels.py    # DB rows → template structures
├── monitor/         # sitemap.py · basic.py · detailed.py · diff.py
├── templates/       # index.html + partials/drawer.html
└── static/          # app.css · app.js
```

## Security notes

- Sitemaps are untrusted input. Parsing currently uses stdlib `ElementTree`; before exposing
  publicly, switch to `defusedxml` (already in `requirements.txt`) to guard against XXE.
- The crawler sets a `User-Agent` and timeouts; be a good citizen and use sane intervals.

## License

MIT
