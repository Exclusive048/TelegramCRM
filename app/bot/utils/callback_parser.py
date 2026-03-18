from __future__ import annotations

from typing import Sequence

from app.bot.diagnostics import log_callback_parse_failed


def safe_parse(
    data: str | None,
    expected_parts: int,
    expected_types: Sequence[type],
) -> tuple | None:
    if not isinstance(data, str):
        log_callback_parse_failed(
            raw_data=data,
            reason="data_not_string",
            expected_parts=expected_parts,
            expected_types=expected_types,
        )
        return None
    if expected_parts <= 0 or len(expected_types) != expected_parts:
        log_callback_parse_failed(
            raw_data=data,
            reason="invalid_expected_shape",
            expected_parts=expected_parts,
            expected_types=expected_types,
        )
        return None

    parts = data.split(":")
    if len(parts) != expected_parts:
        log_callback_parse_failed(
            raw_data=data,
            reason="unexpected_parts_count",
            expected_parts=expected_parts,
            expected_types=expected_types,
        )
        return None

    parsed: list[object] = []
    for raw, expected_type in zip(parts, expected_types, strict=True):
        if expected_type is str:
            parsed.append(raw)
            continue
        if expected_type is int:
            try:
                parsed.append(int(raw))
            except (TypeError, ValueError):
                log_callback_parse_failed(
                    raw_data=data,
                    reason="int_cast_failed",
                    expected_parts=expected_parts,
                    expected_types=expected_types,
                )
                return None
            continue
        try:
            parsed.append(expected_type(raw))
        except Exception:
            log_callback_parse_failed(
                raw_data=data,
                reason=f"{expected_type.__name__}_cast_failed",
                expected_parts=expected_parts,
                expected_types=expected_types,
            )
            return None

    return tuple(parsed)
