from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.rate_limit import limiter
from app.api.routes import leads as leads_router
from app.api.routes import yukassa_webhook as yukassa_router
from app.services.message_deletion_service import MessageDeletionService
from app.telegram.safe_sender import ChatRateLimiter, TelegramSafeSender


def create_app(bot: Bot, sender: TelegramSafeSender, *, redis_url: str) -> FastAPI:
    app = FastAPI(docs_url="/api/docs", redoc_url=None, title="TelegramCRM API", version="1.0")
    app.state.bot = bot
    app.state.sender = sender
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(leads_router.router, prefix="/api/v1")
    app.include_router(yukassa_router.router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        from app.db.database import AsyncSessionLocal
        from redis.asyncio import Redis

        try:
            async with AsyncSessionLocal() as s:
                await s.execute(text("SELECT 1"))
            r = Redis.from_url(redis_url)
            await r.ping()
            await r.aclose()
            return {"status": "ok"}
        except Exception as e:
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)

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
