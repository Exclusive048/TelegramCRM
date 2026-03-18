from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat
from loguru import logger

from app.bootstrap import (
    clear_webhook,
    configure_event_loop,
    init_storage,
    start_bot_with_retry,
)
from app.bot.middlewares.tracing_middleware import HandlerTraceMiddleware, UpdateTraceMiddleware
from app.core.config import settings
from master_bot.routers_master import build_master_router
from master_bot.notify import set_master_bot


async def _set_master_commands(bot: Bot) -> None:
    common_commands = [
        BotCommand(command="start", description="Мои CRM-аккаунты и управление"),
    ]
    await bot.set_my_commands(common_commands, scope=BotCommandScopeAllPrivateChats())

    if settings.master_admin_tg_id:
        admin_commands = [
            BotCommand(command="start", description="Открыть меню"),
            BotCommand(command="clients", description="Список клиентов (админ)"),
            BotCommand(command="stats", description="Статистика сервиса (админ)"),
        ]
        await bot.set_my_commands(
            admin_commands,
            scope=BotCommandScopeChat(chat_id=settings.master_admin_tg_id),
        )


async def _resolve_storage_redis_enabled() -> bool:
    if not settings.use_redis:
        return False
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        await client.aclose()
        return True
    except Exception as exc:
        logger.warning(
            "Redis unavailable for master bot FSM storage; fallback to MemoryStorage: {}",
            exc,
        )
        return False


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
    await _set_master_commands(bot)

    set_master_bot(bot)

    use_redis_storage = await _resolve_storage_redis_enabled()
    storage = init_storage(
        use_redis=use_redis_storage,
        redis_url=settings.redis_url,
    )
    dp = Dispatcher(storage=storage)
    dp.update.outer_middleware(UpdateTraceMiddleware(bot_role="master_bot"))
    dp.message.middleware(HandlerTraceMiddleware(bot_role="master_bot"))
    dp.callback_query.middleware(HandlerTraceMiddleware(bot_role="master_bot"))
    dp.include_router(build_master_router())

    configure_event_loop()
    await start_bot_with_retry(dp, bot)


if __name__ == "__main__":
    asyncio.run(main())
