from __future__ import annotations

from aiogram.types import CallbackQuery, Message

from app.bot.diagnostics import log_guard_rejected
from app.core.permissions import is_crm_admin
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.telegram.safe_sender import TelegramSafeSender


async def resolve_admin_context(event, tenant, sender: TelegramSafeSender) -> tuple[int, int] | None:
    """Returns (group_id, user_id) or None if no access."""
    message = event.message if isinstance(event, CallbackQuery) else event
    if not isinstance(message, Message) or not message.from_user:
        log_guard_rejected("admin_context_message_missing")
        return None

    user_id = message.from_user.id
    if isinstance(event, CallbackQuery) and event.from_user:
        user_id = event.from_user.id

    group_id = tenant.group_id if tenant else None
    if not group_id:
        group_id = message.chat.id if message.chat.id < 0 else None
    if not group_id:
        log_guard_rejected("admin_context_group_missing", user_id=user_id)
        return None

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        tenant_id = tenant.id if tenant else None
        if not await is_crm_admin(
            sender,
            repo,
            group_id,
            user_id,
            tenant_id=tenant_id,
        ):
            log_guard_rejected(
                "crm_admin_required",
                group_id=group_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            return None
    return group_id, user_id

