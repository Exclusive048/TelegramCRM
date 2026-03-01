import asyncio
import uvicorn
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from app.core.config import settings
from app.api.routes import leads as leads_router
from app.bot.handlers import lead_callbacks, setup, cabinet, panel  # FIXED #14
from app.bot.middlewares.sender_middleware import SenderMiddleware
from app.services.message_deletion_service import MessageDeletionService
from app.services.reminder_service import ReminderService
from app.telegram.safe_sender import TelegramSafeSender, ChatRateLimiter


def create_app(bot: Bot, sender: TelegramSafeSender) -> FastAPI:
    app = FastAPI(title="CRM Bot API", version="2.0.0", docs_url="/api/docs")
    app.state.bot = bot
    app.state.sender = sender
    app.include_router(leads_router.router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


async def start_bot(dp: Dispatcher, bot: Bot):
    logger.info("Starting bot polling...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


async def main():
    logger.info("Start TelegramCRM bot")
    logger.info("Starting TelegramCRM bot. Ensure 'python migrate.py dev' was run before first launch.")  # FIXED #4

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))

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

    # Storage: Redis если USE_REDIS=true, иначе Memory
    if settings.use_redis:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(settings.redis_url)
        logger.info("FSM storage: Redis")
    else:
        storage = MemoryStorage()
        logger.info("FSM storage: Memory. Not recommended for production! For using Redis, set USE_REDIS=true and provide REDIS_URL in .env")

    dp = Dispatcher(storage=storage)
    dp["sender"] = sender
    dp.update.middleware(SenderMiddleware())
    dp.include_router(lead_callbacks.router)
    dp.include_router(setup.router)
    dp.include_router(cabinet.router)
    dp.include_router(panel.router)  # FIXED #14

    await deletion_service.start(sender)
    await ReminderService.start_scheduler(sender)

    app = create_app(bot, sender)
    config = uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="info")
    server = uvicorn.Server(config)

    await asyncio.gather(start_bot(dp, bot), server.serve())


if __name__ == "__main__":
    asyncio.run(main())
