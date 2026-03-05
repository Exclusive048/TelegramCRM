from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger

from app.db.database import AsyncSessionLocal
from app.db.repositories.tenant_repository import TenantRepository


class TenantMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:

        # ── Логируем ВСЕ входящие апдейты ─────────────────────────────────────
        if isinstance(event, Message):
            logger.debug(
                f"[MW] MESSAGE: chat_id={event.chat.id} "
                f"chat_type={event.chat.type} "
                f"thread_id={event.message_thread_id} "
                f"from={event.from_user.id if event.from_user else None} "
                f"text={event.text!r} "
                f"reply_to={event.reply_to_message.message_id if event.reply_to_message else None}"
            )
        elif isinstance(event, CallbackQuery):
            logger.debug(
                f"[MW] CALLBACK: data={event.data!r} "
                f"from={event.from_user.id if event.from_user else None} "
                f"chat_id={event.message.chat.id if event.message else None}"
            )
        else:
            logger.debug(f"[MW] OTHER EVENT: type={type(event).__name__}")

        # ── Исключённые команды — пропускаем без проверки тенанта ─────────────
        EXCLUDED_COMMANDS = {"/start", "/pay", "/help", "/setup"}
        if isinstance(event, Message) and event.text:
            command = event.text.split()[0]
            if command in EXCLUDED_COMMANDS:
                logger.debug(f"[MW] excluded command={command}, skip tenant check")
                return await handler(event, data)

        # ── Определяем chat_id ─────────────────────────────────────────────────
        chat_id = None
        if isinstance(event, Message):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery) and event.message:
            chat_id = event.message.chat.id

        # ── Личный чат (chat_id > 0) — пропускаем без проверки тенанта ────────
        if not chat_id or chat_id > 0:
            logger.debug(f"[MW] private/no chat (chat_id={chat_id}), skip tenant check → pass to handler")
            return await handler(event, data)

        # ── Групповой чат — проверяем тенанта ─────────────────────────────────
        async with AsyncSessionLocal() as session:
            repo = TenantRepository(session)
            tenant = await repo.get_by_group_id(chat_id)

            if not tenant:
                logger.warning(f"[MW] no tenant for chat_id={chat_id} → DROP")
                return

            logger.debug(f"[MW] tenant found: id={tenant.id} is_active={tenant.is_active}")

            # Авто-деактивация при истечении подписки
            if tenant.is_active and tenant.subscription_until:
                if tenant.subscription_until < datetime.now(timezone.utc):
                    await repo.deactivate(tenant.id)
                    await session.commit()
                    tenant.is_active = False
                    logger.info(f"[MW] tenant {tenant.id} auto-deactivated")

            if not tenant.is_active:
                try:
                    text = "⛔️ Подписка неактивна. Напишите /pay для оплаты."
                    if isinstance(event, Message):
                        await event.answer(text)
                    elif isinstance(event, CallbackQuery):
                        await event.answer(text, show_alert=True)
                except Exception:
                    pass
                logger.debug("[MW] tenant inactive → DROP")
                return

            data["tenant"] = tenant
            logger.debug("[MW] tenant OK → pass to handler")

        return await handler(event, data)