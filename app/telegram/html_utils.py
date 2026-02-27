from __future__ import annotations

import html
from typing import Any


def html_escape(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))
