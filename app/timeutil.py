"""Time helpers."""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC timestamp, replacing the deprecated datetime.utcnow().

    Kept naive (tzinfo stripped) on purpose: SQLite has no native timezone type and
    returns naive datetimes on read, so storing naive UTC keeps all comparisons consistent.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
