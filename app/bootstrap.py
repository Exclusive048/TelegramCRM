from __future__ import annotations

import asyncio
import uuid

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.rate_limit import limiter
from app.api.routes import leads as leads_router
from app.api.routes import yukassa_webhook as yukassa_router
from app.health_checks import run_readiness_checks
from app.services.message_deletion_service import MessageDeletionService
from app.telegram.safe_sender import ChatRateLimiter, TelegramSafeSender

LEADS_BODY_LIMIT_BYTES = 512 * 1024
YUKASSA_BODY_LIMIT_BYTES = 64 * 1024
_BODY_LIMITS_BY_ROUTE: dict[tuple[str, str], int] = {
    ("POST", "/api/v1/leads"): LEADS_BODY_LIMIT_BYTES,
    ("POST", "/api/v1/leads/tilda"): LEADS_BODY_LIMIT_BYTES,
    ("POST", "/api/v1/webhook/yukassa"): YUKASSA_BODY_LIMIT_BYTES,
}


def _normalize_path(path: str) -> str:
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def _get_body_limit(request: Request) -> int | None:
    key = (request.method.upper(), _normalize_path(request.url.path))
    return _BODY_LIMITS_BY_ROUTE.get(key)


def create_app(bot: Bot, sender: TelegramSafeSender, *, redis_url: str, use_redis: bool) -> FastAPI:
    app = FastAPI(docs_url="/api/docs", redoc_url=None, title="TelegramCRM API", version="1.0")
    app.state.bot = bot
    app.state.sender = sender
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(leads_router.router, prefix="/api/v1")
    app.include_router(yukassa_router.router, prefix="/api/v1")

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        body_limit = _get_body_limit(request)
        if body_limit is not None:
            raw_body = await request.body()
            if len(raw_body) > body_limit:
                response = JSONResponse(
                    status_code=413,
                    content={"error": "payload_too_large", "request_id": request_id},
                )
                response.headers["X-Request-ID"] = request_id
                return response

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            tenant = getattr(request.state, "tenant", None)
            tenant_id = getattr(tenant, "id", None)

        logger.opt(exception=exc).error(
            "Unhandled API error request_id={} method={} path={} tenant_id={}",
            request_id,
            request.method,
            request.url.path,
            tenant_id,
        )
        response = JSONResponse(
            status_code=500,
            content={"error": "internal_error", "request_id": request_id},
        )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/live")
    async def liveness():
        return {"status": "ok"}

    @app.get("/ready")
    async def readiness():
        from app.db.database import AsyncSessionLocal

        result = await run_readiness_checks(
            use_redis=use_redis,
            redis_url=redis_url,
            session_factory=AsyncSessionLocal,
        )
        if result["status"] == "ok":
            return result
        return JSONResponse(result, status_code=503)

    @app.get("/health")
    async def health():
        return await readiness()

    return app


async def start_bot_with_retry(dp: Dispatcher, bot: Bot) -> None:
    from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

    backoff = 1
    while True:
        try:
            logger.info("Bot polling started")
            backoff = 1
            await dp.start_polling(
                bot,
                allowed_updates=["message", "callback_query"],
                handle_signals=False,
                drop_pending_updates=False,
            )
        except (TelegramNetworkError, TelegramRetryAfter, ConnectionError) as e:
            logger.warning(f"Bot polling network error: {e}. Retry in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            logger.exception(f"Bot polling fatal error (not retriable): {e}")
            raise


def init_sender(
    bot: Bot,
    *,
    use_redis: bool,
    redis_url: str,
) -> tuple[TelegramSafeSender, MessageDeletionService]:
    if use_redis:
        from redis.asyncio import Redis

        redis_client = Redis.from_url(redis_url, decode_responses=True)
        deletion_service = MessageDeletionService(redis_client)
        logger.info("Message deletion service: Redis")
    else:
        deletion_service = MessageDeletionService()
        logger.warning("Message deletion service: in-memory (not persistent)")

    sender = TelegramSafeSender(
        bot,
        limiter=ChatRateLimiter(min_delay_sec=1.1),
        max_attempts=6,
        deletion_service=deletion_service,
    )
    return sender, deletion_service


def init_storage(*, use_redis: bool, redis_url: str) -> BaseStorage:
    if use_redis:
        from aiogram.fsm.storage.redis import RedisStorage

        storage = RedisStorage.from_url(
            redis_url,
            state_ttl=86400 * 7,
            data_ttl=86400 * 7,
        )
        logger.info("FSM storage: Redis")
    else:
        storage = MemoryStorage()
        logger.warning("FSM storage: Memory. Not recommended for production!")
    return storage


def handle_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    msg = context.get("exception", context["message"])
    logger.error(f"Unhandled asyncio exception: {msg}")


def configure_event_loop() -> None:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(handle_exception)


async def clear_webhook(bot: Bot, *, name: str) -> None:
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        logger.info(f"{name} webhook cleared")
    except Exception as e:
        logger.warning(f"Could not clear {name} webhook: {e}")
