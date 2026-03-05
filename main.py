import asyncio
import sys
import uvicorn
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from app.core.config import settings
from app.api.routes import leads as leads_router
from app.api.routes import yukassa_webhook as yukassa_router
from app.bot.handlers import lead_callbacks, setup, cabinet, panel
from app.bot.middlewares.sender_middleware import SenderMiddleware
from app.services.message_deletion_service import MessageDeletionService
from app.services.reminder_service import ReminderService
from app.services.subscription_scheduler import start_subscription_scheduler
from app.telegram.safe_sender import TelegramSafeSender, ChatRateLimiter


def create_app(bot: Bot, sender: TelegramSafeSender) -> FastAPI:
    app = FastAPI(docs_url="/api/docs", redoc_url=None, title="TelegramCRM API", version="1.0")
    app.state.bot = bot
    app.state.sender = sender
    app.include_router(leads_router.router, prefix="/api/v1")
    app.include_router(yukassa_router.router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


async def start_bot_with_retry(dp: Dispatcher, bot: Bot):
    from aiohttp import ClientError
    from aiogram.exceptions import TelegramNetworkError

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
        except (TelegramNetworkError, ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Bot polling network error: {e}. Retry in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            logger.error(f"Bot polling fatal error: {e}. Retry in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def start_api_server(app: FastAPI):
    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        timeout_keep_alive=30,
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except Exception as e:
        logger.error(f"API server error: {e}")
        raise


def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    logger.error(f"Unhandled asyncio exception: {msg}")


async def main():
    logger.info("Start TelegramCRM bot")
    logger.info("Starting TelegramCRM bot. Ensure 'python migrate.py dev' was run before first launch.")

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))

    # Сбросить вебхук при старте чтобы polling работал корректно
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        logger.info("Webhook cleared")
    except Exception as e:
        logger.warning(f"Could not clear webhook: {e}")

    deletion_service = None
    if settings.use_redis:
        from redis.asyncio import Redis
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
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

    if settings.use_redis:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(settings.redis_url)
        logger.info("FSM storage: Redis")
    else:
        storage = MemoryStorage()
        logger.warning("FSM storage: Memory. Not recommended for production!")

    dp = Dispatcher(storage=storage)
    dp["sender"] = sender
    dp.update.middleware(SenderMiddleware())
    from app.bot.middlewares.tenant_middleware import TenantMiddleware
    dp.update.outer_middleware(TenantMiddleware())
    dp.include_router(lead_callbacks.router)
    dp.include_router(setup.router)
    dp.include_router(cabinet.router)
    dp.include_router(panel.router)

    await deletion_service.start(sender)
    await ReminderService.start_scheduler(sender)
    start_subscription_scheduler()

    app = create_app(bot, sender)

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)

    master_tasks = []
    if settings.master_bot_token:
        from aiogram.fsm.storage.redis import RedisStorage
        from master_bot.handlers import router as master_router
        from master_bot.admin import router as admin_router
        from master_bot.notify import set_master_bot

        master_bot_instance = Bot(
            token=settings.master_bot_token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )

        # Сбросить вебхук мастер-бота
        try:
            await master_bot_instance.delete_webhook(drop_pending_updates=False)
            logger.info("Master bot webhook cleared")
        except Exception as e:
            logger.warning(f"Could not clear master bot webhook: {e}")

        set_master_bot(master_bot_instance)
        master_storage = RedisStorage.from_url(settings.redis_url)
        master_dp = Dispatcher(storage=master_storage)
        master_dp.include_router(admin_router)
        master_dp.include_router(master_router)
        master_tasks.append(
            asyncio.create_task(
                start_bot_with_retry(master_dp, master_bot_instance)
            )
        )
        logger.info("Master bot started")

    await asyncio.gather(
        start_bot_with_retry(dp, bot),
        start_api_server(app),
        *master_tasks,
        return_exceptions=False,
    )


if __name__ == "__main__":
    asyncio.run(main())