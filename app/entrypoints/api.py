from __future__ import annotations

import asyncio

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from fastapi import FastAPI
from loguru import logger

from app.bootstrap import configure_event_loop, create_app, init_sender
from app.core.config import settings


async def start_api_server(app: FastAPI) -> None:
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


async def main() -> None:
    logger.info("Starting API process")
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    sender, _ = init_sender(
        bot,
        use_redis=settings.use_redis,
        redis_url=settings.redis_url,
    )
    app = create_app(bot, sender)
    configure_event_loop()
    await start_api_server(app)


if __name__ == "__main__":
    asyncio.run(main())
