from __future__ import annotations

from aiogram import Bot
from loguru import logger

_master_bot: Bot | None = None


def set_master_bot(bot: Bot) -> None:
    global _master_bot
    _master_bot = bot


async def notify_admin(text: str) -> None:
    """Отправляет уведомление владельцу в мастер-бот."""
    from app.core.config import settings

    if not _master_bot or not settings.master_admin_tg_id:
        return
    try:
        await _master_bot.send_message(
            chat_id=settings.master_admin_tg_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"master notify failed: {e}")


async def notify_tenant_owner(owner_tg_id: int, text: str) -> None:
    """Отправляет уведомление конкретному клиенту (например о скором истечении)."""
    if not _master_bot:
        return
    try:
        await _master_bot.send_message(
            chat_id=owner_tg_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"notify_tenant_owner failed owner={owner_tg_id}: {e}")
