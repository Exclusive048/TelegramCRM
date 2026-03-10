from __future__ import annotations

from typing import Sequence


def safe_parse(
    data: str | None,
    expected_parts: int,
    expected_types: Sequence[type],
) -> tuple | None:
    if not isinstance(data, str):
        return None
    if expected_parts <= 0 or len(expected_types) != expected_parts:
        return None

    parts = data.split(":")
    if len(parts) != expected_parts:
        return None

    parsed: list[object] = []
    for raw, expected_type in zip(parts, expected_types):
        if expected_type is str:
            parsed.append(raw)
            continue
        if expected_type is int:
            try:
                parsed.append(int(raw))
            except (TypeError, ValueError):
                return None
            continue
        try:
            parsed.append(expected_type(raw))
        except Exception:
            return None

    return tuple(parsed)
