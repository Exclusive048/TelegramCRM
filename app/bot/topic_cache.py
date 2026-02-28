from __future__ import annotations

from time import monotonic


_CACHE: dict[int, tuple[float, dict[str, int]]] = {}
TTL_SEC = 600


def get_cached(chat_id: int) -> dict[str, int] | None:
    item = _CACHE.get(chat_id)
    if not item:
        return None
    expires_at, mapping = item
    if monotonic() >= expires_at:
        _CACHE.pop(chat_id, None)
        return None
    return mapping


def set_cached(chat_id: int, mapping: dict[str, int]) -> None:
    _CACHE[chat_id] = (monotonic() + TTL_SEC, dict(mapping))


def invalidate(chat_id: int) -> None:
    _CACHE.pop(chat_id, None)
