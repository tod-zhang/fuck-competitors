"""Politeness layer (app/monitor/fetch.py): robots.txt, conditional GETs, 429/403 backoff,
per-host rate spacing. Uses httpx.MockTransport — no network. Each test uses a distinct host
so the module-level robots/cooldown caches don't bleed between cases.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.monitor import fetch  # noqa: E402


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), headers=fetch.DEFAULT_HEADERS)


def test_robots_disallow_blocks():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret")
        return httpx.Response(200, text="ok")

    c = _client(handler)
    assert fetch.polite_get(c, "http://robots-a.test/public").status_code == 200
    try:
        fetch.polite_get(c, "http://robots-a.test/secret/x")
        assert False, "disallowed path should raise Blocked"
    except fetch.Blocked:
        pass


def test_429_opens_cooldown():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429, headers={"Retry-After": "120"})

    c = _client(handler)
    try:
        fetch.polite_get(c, "http://busy-b.test/p")
        assert False, "429 should raise Blocked"
    except fetch.Blocked:
        pass
    # host is now cooling down -> next call short-circuits before any request
    try:
        fetch.polite_get(c, "http://busy-b.test/other")
        assert False, "cooling-down host should raise Blocked immediately"
    except fetch.Blocked:
        pass


def test_403_opens_cooldown():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403)

    c = _client(handler)
    try:
        fetch.polite_get(c, "http://forbid-c.test/p")
        assert False, "403 should raise Blocked"
    except fetch.Blocked:
        pass
    assert "forbid-c.test" in fetch._cooldown_until


def test_conditional_304_passthrough():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.headers.get("If-None-Match") == 'W/"v1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"ETag": 'W/"v1"'}, text="body")

    c = _client(handler)
    r1 = fetch.polite_get(c, "http://cond-d.test/p")
    assert r1.status_code == 200 and r1.headers.get("ETag") == 'W/"v1"'

    fetch._next_allowed.pop("cond-d.test", None)  # skip the rate-limit wait for a fast test
    r2 = fetch.polite_get(c, "http://cond-d.test/p", conditional={"If-None-Match": 'W/"v1"'})
    assert r2.status_code == 304


def test_per_host_spacing():
    host = "spacing-e.test"
    fetch._next_allowed.pop(host, None)
    first = fetch._reserve_slot(host, 2.0)
    second = fetch._reserve_slot(host, 2.0)
    assert first <= 0.01, first                 # first request fires immediately
    assert 1.9 <= second <= 2.1, second         # second is spaced by the interval


if __name__ == "__main__":
    test_robots_disallow_blocks()
    test_429_opens_cooldown()
    test_403_opens_cooldown()
    test_conditional_304_passthrough()
    test_per_host_spacing()
    print("ok: robots, 429/403 cooldown, 304 conditional, per-host spacing")
