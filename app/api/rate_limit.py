from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def ip_and_api_key(request: Request) -> str:
    ip = get_remote_address(request)
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        return f"{ip}:{api_key}"
    return ip


limiter = Limiter(key_func=ip_and_api_key)
