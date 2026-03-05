from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from loguru import logger

from app.bootstrap import (
    clear_webhook,
    configure_event_loop,
    init_storage,
    start_bot_with_retry,
)
from app.core.config import settings
from master_bot.routers_master import build_master_router
from master_bot.notify import set_master_bot


async def main() -> None:
    logger.info("Starting master bot process")

    if not settings.master_bot_token:
        logger.warning("MASTER_BOT_TOKEN is empty. Master bot polling disabled.")
        await asyncio.Event().wait()
        return

    bot = Bot(
        token=settings.master_bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    await clear_webhook(bot, name="Master bot")

    set_master_bot(bot)

    storage = init_storage(use_redis=True, redis_url=settings.redis_url)
    dp = Dispatcher(storage=storage)
    dp.include_router(build_master_router())

    configure_event_loop()
    await start_bot_with_retry(dp, bot)


if __name__ == "__main__":
    asyncio.run(main())
