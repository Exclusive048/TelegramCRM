from __future__ import annotations

from pathlib import Path

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def ip_and_api_key(request: Request) -> str:
    ip = get_remote_address(request)
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        return f"{ip}:{api_key}"
    return ip


_SLOWAPI_CONFIG_FILE = Path(__file__).with_name("slowapi.env")

limiter = Limiter(
    key_func=ip_and_api_key,
    config_filename=str(_SLOWAPI_CONFIG_FILE),
)
