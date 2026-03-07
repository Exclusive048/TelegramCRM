from __future__ import annotations

from datetime import datetime, timezone


def _naive(dt: datetime | None) -> datetime | None:
    """Strip timezone info for compatibility with TIMESTAMP WITHOUT TIME ZONE."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

