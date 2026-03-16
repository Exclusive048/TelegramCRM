from __future__ import annotations

from typing import Any, Callable

from loguru import logger
from sqlalchemy import text


async def run_readiness_checks(
    *,
    use_redis: bool,
    redis_url: str,
    session_factory: Callable[[], Any],
    redis_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """
    Readiness probe:
    - DB is always required.
    - Redis is required only when use_redis=True.
    """
    dependencies: dict[str, str] = {
        "database": "unknown",
        "redis": "disabled" if not use_redis else "unknown",
    }

    db_ok = False
    redis_ok = not use_redis

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        dependencies["database"] = "ok"
        db_ok = True
    except Exception as exc:
        dependencies["database"] = "error"
        logger.exception(f"Health check database probe failed: {exc}")

    if use_redis:
        client = None
        try:
            if redis_factory is None:
                from redis.asyncio import Redis

                client = Redis.from_url(redis_url)
            else:
                client = redis_factory(redis_url)
            await client.ping()
            dependencies["redis"] = "ok"
            redis_ok = True
        except Exception as exc:
            dependencies["redis"] = "error"
            logger.exception(f"Health check redis probe failed: {exc}")
        finally:
            if client is not None and hasattr(client, "aclose"):
                try:
                    await client.aclose()
                except Exception:
                    pass

    if db_ok and redis_ok:
        return {
            "status": "ok",
            "dependencies": dependencies,
        }

    if not db_ok:
        code = "db_unavailable"
    else:
        code = "redis_unavailable"

    return {
        "status": "error",
        "code": code,
        "dependencies": dependencies,
    }
