import asyncio
import uvicorn
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from app.core.config import settings
from app.db.database import create_tables
from app.api.routes import leads as leads_router
from app.bot.handlers import lead_callbacks, setup, cabinet


def create_app(bot: Bot) -> FastAPI:
    app = FastAPI(title="CRM Bot API", version="2.0.0", docs_url="/api/docs")
    app.state.bot = bot
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
    await create_tables()
    logger.info("Tables created or already exist")

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))

    # Storage: Redis если USE_REDIS=true, иначе Memory
    if settings.use_redis:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(settings.redis_url)
        logger.info("FSM storage: Redis")
    else:
        storage = MemoryStorage()
        logger.info("FSM storage: Memory. Not recommended for production! For using Redis, set USE_REDIS=true and provide REDIS_URL in .env")

    dp = Dispatcher(storage=storage)
    dp.include_router(lead_callbacks.router)
    dp.include_router(setup.router)
    dp.include_router(cabinet.router)

    app = create_app(bot)
    config = uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="info")
    server = uvicorn.Server(config)

    await asyncio.gather(start_bot(dp, bot), server.serve())


if __name__ == "__main__":
    asyncio.run(main())
