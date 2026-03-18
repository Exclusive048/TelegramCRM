from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger

from app.bot.diagnostics import TG_MIDDLEWARE_ENTER, TG_MIDDLEWARE_EXIT, emit_tg_event, log_guard_rejected
from app.db.database import AsyncSessionLocal
from app.db.repositories.tenant_repository import TenantRepository

EXCLUDED_COMMANDS = {"/start", "/pay", "/help", "/setup"}


class TenantMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        emit_tg_event(TG_MIDDLEWARE_ENTER, middleware="tenant")

        if isinstance(event, Message) and event.text:
            command = event.text.split()[0]
            if command in EXCLUDED_COMMANDS:
                emit_tg_event(
                    TG_MIDDLEWARE_EXIT,
                    middleware="tenant",
                    outcome="skipped",
                    skip_reason="excluded_command",
                    command=command,
                )
                return await handler(event, data)

        chat_id: int | None = None
        if isinstance(event, Message):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery) and event.message:
            chat_id = event.message.chat.id

        if chat_id is None or chat_id > 0:
            emit_tg_event(
                TG_MIDDLEWARE_EXIT,
                middleware="tenant",
                outcome="skipped",
                skip_reason="private_or_missing_chat",
            )
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            repo = TenantRepository(session)
            tenant = await repo.get_by_group_id(chat_id)
            if not tenant:
                log_guard_rejected("tenant_not_found", middleware="tenant", chat_id=chat_id)
                emit_tg_event(
                    TG_MIDDLEWARE_EXIT,
                    middleware="tenant",
                    outcome="rejected",
                    rejection_reason="tenant_not_found",
                    chat_id=chat_id,
                )
                return None

            if tenant.is_active and tenant.subscription_until:
                if tenant.subscription_until < datetime.now(timezone.utc):
                    await repo.deactivate(tenant.id)
                    await session.commit()
                    tenant.is_active = False
                    logger.info("Tenant {} auto-deactivated by middleware", tenant.id)

            if not tenant.is_active:
                try:
                    text = "⚠️ Подписка неактивна. Администратору группы нужно выполнить /pay."
                    if isinstance(event, Message):
                        await event.answer(text)
                    elif isinstance(event, CallbackQuery):
                        await event.answer(text, show_alert=True)
                except Exception as exc:
                    logger.warning(
                        "Inactive tenant notification failed chat_id={} err={}",
                        chat_id,
                        exc,
                    )

                log_guard_rejected(
                    "tenant_inactive",
                    middleware="tenant",
                    tenant_id=tenant.id,
                    chat_id=chat_id,
                )
                emit_tg_event(
                    TG_MIDDLEWARE_EXIT,
                    middleware="tenant",
                    outcome="rejected",
                    rejection_reason="tenant_inactive",
                    tenant_id=tenant.id,
                    chat_id=chat_id,
                )
                return None

            data["tenant"] = tenant

        emit_tg_event(
            TG_MIDDLEWARE_EXIT,
            middleware="tenant",
            outcome="pass",
            tenant_id=getattr(data.get("tenant"), "id", None),
        )
        return await handler(event, data)
