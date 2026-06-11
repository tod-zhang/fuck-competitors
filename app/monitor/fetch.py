"""Polite, shared HTTP fetching for every crawler.

All outbound monitor requests go through here so the manners are consistent: identify
ourselves with one honest User-Agent, obey robots.txt, space out requests to the same host,
and back off when a site pushes back (429/403). That politeness is what keeps a many-site
SaaS crawl from getting the server's IP blocked.

State (robots cache, per-host next-allowed time, per-host cooldowns) lives in module-level
dicts behind a lock. The app runs a single worker, but APScheduler fires competitor checks on
background threads, so several hosts — and occasionally the same host — can be in flight at once.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..config import settings

DEFAULT_HEADERS = {
    "User-Agent": settings.user_agent,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class Blocked(Exception):
    """A host must not be fetched right now: robots disallow, an active cooldown, or a 403/429."""


_lock = threading.Lock()
_robots: dict[str, RobotFileParser | None] = {}   # host -> parser  (None = no robots / allow all)
_next_allowed: dict[str, float] = {}              # host -> monotonic time the next request may fire
_cooldown_until: dict[str, float] = {}            # host -> monotonic time the host stays blocked until


def make_client(timeout: int) -> httpx.Client:
    """An httpx client pre-loaded with our identity headers and redirect-following."""
    return httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True)


def _robots_for(client: httpx.Client, scheme: str, host: str) -> RobotFileParser | None:
    with _lock:
        if host in _robots:
            return _robots[host]
    parser: RobotFileParser | None = RobotFileParser()
    try:
        resp = client.get(f"{scheme or 'https'}://{host}/robots.txt")
        if resp.status_code >= 400:
            # No robots / server error -> treat as "no rules". This is deliberately lenient: a
            # flaky robots endpoint shouldn't silently halt monitoring of an otherwise-public site.
            parser = None
        else:
            parser.parse(resp.text.splitlines())
    except Exception:
        parser = None
    with _lock:
        _robots[host] = parser
    return parser


def _reserve_slot(host: str, interval: float) -> float:
    """Claim the next request slot for `host`; return seconds to sleep before firing it."""
    with _lock:
        now = time.monotonic()
        fire_at = max(now, _next_allowed.get(host, 0.0))
        _next_allowed[host] = fire_at + interval
    return fire_at - now


def _ensure_not_cooling(host: str) -> None:
    with _lock:
        if time.monotonic() < _cooldown_until.get(host, 0.0):
            raise Blocked(f"{host} is cooling down after a 403/429")


def _start_cooldown(host: str, seconds: float) -> None:
    with _lock:
        _cooldown_until[host] = time.monotonic() + seconds


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))   # numeric form; HTTP-date form falls back to the default cooldown
    except ValueError:
        return None


def polite_get(client: httpx.Client, url: str, *, conditional: dict | None = None) -> httpx.Response:
    """GET `url` politely, returning the response. Raises Blocked when robots/cooldown/403/429 say no.

    `conditional` may carry If-None-Match / If-Modified-Since headers; a 304 response is returned
    as-is for the caller to treat as "unchanged".
    """
    parts = urlsplit(url)
    host = parts.netloc
    _ensure_not_cooling(host)

    interval = settings.crawl_delay_seconds
    if settings.respect_robots:
        robots = _robots_for(client, parts.scheme, host)
        if robots is not None:
            if not robots.can_fetch(settings.user_agent, url):
                raise Blocked(f"robots.txt disallows {url}")
            crawl_delay = robots.crawl_delay(settings.user_agent)
            if crawl_delay:
                interval = max(interval, float(crawl_delay))

    wait = _reserve_slot(host, interval)
    if wait > 0:
        time.sleep(wait)

    resp = client.get(url, headers=conditional or None)

    if resp.status_code == 429:
        cooldown = _retry_after_seconds(resp.headers.get("Retry-After")) or settings.block_cooldown_seconds
        _start_cooldown(host, cooldown)
        raise Blocked(f"429 Too Many Requests from {host}")
    if resp.status_code == 403:
        _start_cooldown(host, settings.block_cooldown_seconds)
        raise Blocked(f"403 Forbidden from {host}")
    return resp
