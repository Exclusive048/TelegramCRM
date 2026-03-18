from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from app.bot.constants.ttl import TTL_ERROR_SEC
from app.bot.ui.message_ref import MessageRef
from app.bot.utils.force_reply import cleanup_force_reply, reject_non_force_reply
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.services.lead_service import LeadService
from app.telegram.safe_sender import TelegramSafeSender

from .lead_callbacks_shared import (
    AmountState,
    NO_ACCESS_TEXT,
    RejectState,
    _get_group_id,
    _get_manager,
    _manager_can_act,
    _parse_amount,
)

router = Router(name="crm.lead_callbacks.force_reply")


async def _run_post_commit_sync(
    *,
    session,
    service: LeadService,
    lead_id: int,
    tenant_id: int | None,
    transition: str,
) -> None:
    try:
        logger.info(
            "lead_transition_post_commit_started lead_id={} tenant_id={} transition={} origin=bot_force_reply",
            lead_id,
            tenant_id,
            transition,
        )
        await service.sync_lead_after_transition(lead_id, transition)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception(
            "lead_transition_post_commit_failed lead_id={} tenant_id={} transition={} origin=bot_force_reply",
            lead_id,
            tenant_id,
            transition,
        )


@router.message(AmountState.waiting_for_amount)
async def handle_amount_input(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant) or (message.chat.id if message.chat.id < 0 else None)
    tenant_id = tenant.id if tenant else None
    if not await reject_non_force_reply(message, state, sender):
        return

    amount = _parse_amount(message.text or "")
    if amount is None:
        try:
            await sender.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                thread_id=message.message_thread_id,
            )
        except Exception:
            pass
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="\u26d4\ufe0f \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u0443\u044e \u0441\u0443\u043c\u043c\u0443 (\u0447\u0438\u0441\u043b\u043e \u0431\u043e\u043b\u044c\u0448\u0435 0).",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    data = await state.get_data()
    lead_id = data.get("lead_id")
    target_ref = MessageRef.from_dict(data.get("message_ref"))
    if not lead_id:
        await cleanup_force_reply(sender, state, message)
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, message.from_user.id, tenant_id=tenant_id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return
        lead_obj = await repo.get_by_id(int(lead_id), tenant_id=tenant_id)
        if not lead_obj or not _manager_can_act(manager, lead_obj):
            await cleanup_force_reply(sender, state, message)
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=NO_ACCESS_TEXT,
                ttl_sec=TTL_ERROR_SEC,
            )
            await state.clear()
            return

        service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
        lead = await service.mark_paid(int(lead_id), message.from_user.id, amount, target_ref)
        if lead:
            await session.commit()
            await _run_post_commit_sync(
                session=session,
                service=service,
                lead_id=lead.id,
                tenant_id=tenant_id,
                transition="paid",
            )
        else:
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="\u26a0\ufe0f \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0435\u0440\u0435\u0432\u0435\u0441\u0442\u0438 \u0437\u0430\u044f\u0432\u043a\u0443 \u0432 \u00ab\u041e\u043f\u043b\u0430\u0447\u0435\u043d\u043e\u00bb.",
                ttl_sec=TTL_ERROR_SEC,
            )

    await cleanup_force_reply(sender, state, message)
    await state.clear()


@router.message(RejectState.waiting_for_custom_reason)
async def handle_custom_reject(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant) or (message.chat.id if message.chat.id < 0 else None)
    tenant_id = tenant.id if tenant else None
    if not await reject_non_force_reply(message, state, sender):
        return

    reason = (message.text or "").strip()
    if not reason:
        try:
            await sender.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                thread_id=message.message_thread_id,
            )
        except Exception:
            pass
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="\u26d4\ufe0f \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043f\u0440\u0438\u0447\u0438\u043d\u0443 \u043e\u0442\u043a\u0430\u0437\u0430.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    data = await state.get_data()
    lead_id = data.get("lead_id")
    target_ref = MessageRef.from_dict(data.get("message_ref"))
    if not lead_id:
        await cleanup_force_reply(sender, state, message)
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, message.from_user.id, tenant_id=tenant_id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return
        lead_obj = await repo.get_by_id(int(lead_id), tenant_id=tenant_id)
        if not lead_obj or not _manager_can_act(manager, lead_obj):
            await cleanup_force_reply(sender, state, message)
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=NO_ACCESS_TEXT,
                ttl_sec=TTL_ERROR_SEC,
            )
            await state.clear()
            return

        service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
        lead = await service.reject_lead(int(lead_id), message.from_user.id, reason=reason, source_ref=target_ref)
        if lead:
            await session.commit()
            await _run_post_commit_sync(
                session=session,
                service=service,
                lead_id=lead.id,
                tenant_id=tenant_id,
                transition="reject",
            )
        else:
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="\u26a0\ufe0f \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c \u0437\u0430\u044f\u0432\u043a\u0443.",
                ttl_sec=TTL_ERROR_SEC,
            )

    await cleanup_force_reply(sender, state, message)
    await state.clear()
