from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
from loguru import logger

from app.bootstrap import (
    clear_webhook,
    configure_event_loop,
    init_sender,
    init_storage,
    start_bot_with_retry,
)
from app.bot.middlewares.sender_middleware import SenderMiddleware
from app.bot.middlewares.tenant_middleware import TenantMiddleware
from app.bot.middlewares.tracing_middleware import HandlerTraceMiddleware, UpdateTraceMiddleware
from app.bot.routers_crm import build_crm_router
from app.core.config import settings
from app.services.reminder_service import ReminderService
from app.services.subscription_scheduler import start_subscription_scheduler
from master_bot.notify import set_master_bot


async def _set_crm_commands(bot: Bot) -> None:
    group_commands = [
        BotCommand(command="setup", description="Настроить CRM в группе"),
        BotCommand(command="pay", description="Оплатить или продлить подписку"),
        BotCommand(command="cabinet", description="Открыть кабинет администратора"),
        BotCommand(command="panel", description="Восстановить пульт менеджеров"),
        BotCommand(command="managers", description="Показать команду менеджеров"),
        BotCommand(command="add_manager", description="Добавить менеджера (reply)"),
        BotCommand(command="remove_manager", description="Удалить менеджера (reply)"),
        BotCommand(command="make_admin", description="Назначить CRM-админа (reply)"),
    ]
    private_commands = [
        BotCommand(command="start", description="Как подключить CRM к группе"),
    ]
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())


async def main() -> None:
    logger.info("Starting CRM bot process")
    logger.info("Ensure 'python migrate.py dev' was run before first launch.")

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    await clear_webhook(bot, name="CRM bot")
    await _set_crm_commands(bot)

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
    dp.update.outer_middleware(UpdateTraceMiddleware(bot_role="crm_bot"))
    dp.update.middleware(SenderMiddleware())
    dp.message.middleware(HandlerTraceMiddleware(bot_role="crm_bot"))
    dp.callback_query.middleware(HandlerTraceMiddleware(bot_role="crm_bot"))
    dp.message.outer_middleware(TenantMiddleware())
    dp.callback_query.outer_middleware(TenantMiddleware())
    dp.include_router(build_crm_router())

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
