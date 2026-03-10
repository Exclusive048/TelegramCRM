from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

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

router = Router()


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
            text="⛔️ Введите корректную сумму (число больше 0).",
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
        else:
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⚠️ Не удалось перевести заявку в «Оплачено».",
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
            text="⛔️ Введите причину отказа.",
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
        else:
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⚠️ Не удалось отклонить заявку.",
                ttl_sec=TTL_ERROR_SEC,
            )

    await cleanup_force_reply(sender, state, message)
    await state.clear()
