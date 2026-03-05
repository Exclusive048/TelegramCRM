from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger

from app.db.database import AsyncSessionLocal
from app.db.repositories.tenant_repository import TenantRepository


class TenantMiddleware(BaseMiddleware):
    """
    Проверяет подписку тенанта перед каждым обработчиком.
    Если подписка неактивна — блокирует обработку и сообщает пользователю.
    Добавляет data['tenant'] для использования в хендлерах.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            logger.debug(
                f"INCOMING MESSAGE: chat_id={event.chat.id} "
                f"chat_type={event.chat.type} "
                f"thread_id={event.message_thread_id} "
                f"from={event.from_user.id if event.from_user else None} "
                f"text={event.text!r} "
                f"reply_to={event.reply_to_message.message_id if event.reply_to_message else None}"
            )
        EXCLUDED_COMMANDS = {"/start", "/pay", "/help", "/setup"}

        if isinstance(event, Message) and event.text:
            command = event.text.split()[0]
            if command in EXCLUDED_COMMANDS:
                return await handler(event, data)

        chat_id = None
        if isinstance(event, Message):
            chat_id = event.chat.id
            logger.debug(f"middleware: message chat_id={chat_id} text={event.text!r} thread={event.message_thread_id}")
        elif isinstance(event, CallbackQuery) and event.message:
            chat_id = event.message.chat.id

        if not chat_id or chat_id > 0:
            logger.debug("middleware: private chat or no chat_id, skip tenant check")
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            repo = TenantRepository(session)
            tenant = await repo.get_by_group_id(chat_id)
            logger.debug(f"middleware: tenant={tenant.id if tenant else None} for chat_id={chat_id}")

            if not tenant:
                logger.warning(f"middleware: no tenant for chat_id={chat_id}, dropping")
                return

            # Авто-деактивация при истечении подписки
            if tenant.is_active and tenant.subscription_until:
                if tenant.subscription_until < datetime.now(timezone.utc):
                    await repo.deactivate(tenant.id)
                    await session.commit()
                    tenant.is_active = False
                    logger.info(f"Tenant {tenant.id} auto-deactivated (subscription expired)")

            if not tenant.is_active:
                # Попытаться ответить
                try:
                    text = "⛔️ Подписка неактивна. Напишите /pay для оплаты."
                    if isinstance(event, Message):
                        await event.answer(text)
                    elif isinstance(event, CallbackQuery):
                        await event.answer(text, show_alert=True)
                except Exception:
                    pass
                return  # Хендлер не вызывается

            # Передать тенанта в хендлер
            data["tenant"] = tenant

        return await handler(event, data)
