from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from loguru import logger

from app.bootstrap import (
    clear_webhook,
    configure_event_loop,
    init_sender,
    init_storage,
    start_bot_with_retry,
)
from app.bot.handlers import cabinet, lead_callbacks, panel, setup
from app.bot.middlewares.sender_middleware import SenderMiddleware
from app.bot.middlewares.tenant_middleware import TenantMiddleware
from app.core.config import settings
from app.services.reminder_service import ReminderService
from app.services.subscription_scheduler import start_subscription_scheduler
from master_bot.notify import set_master_bot


async def main() -> None:
    logger.info("Starting CRM bot process")
    logger.info("Ensure 'python migrate.py dev' was run before first launch.")

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    await clear_webhook(bot, name="CRM bot")

    sender, deletion_service = init_sender(
        bot,
        use_redis=settings.use_redis,
        redis_url=settings.redis_url,
    )
    storage = init_storage(
        use_redis=settings.use_redis,
        redis_url=settings.redis_url,
    )

    dp = Dispatcher(storage=storage)
    dp["sender"] = sender
    dp.update.middleware(SenderMiddleware())
    dp.update.outer_middleware(TenantMiddleware())
    dp.include_router(lead_callbacks.router)
    dp.include_router(setup.router)
    dp.include_router(cabinet.router)
    dp.include_router(panel.router)

    if settings.master_bot_token:
        master_bot = Bot(
            token=settings.master_bot_token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        set_master_bot(master_bot)
        logger.info("Master bot notifier attached")

    await deletion_service.start(sender)
    await ReminderService.start_scheduler(sender)
    start_subscription_scheduler()

    configure_event_loop()
    await start_bot_with_retry(dp, bot)


if __name__ == "__main__":
    asyncio.run(main())
