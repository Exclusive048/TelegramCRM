from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from loguru import logger

from app.bot.constants.ttl import TTL_MENU_SEC
from app.bot.keyboards.lead_keyboards import make_reject_reason_keyboard
from app.bot.ui.message_ref import MessageRef
from app.bot.utils.force_reply import start_force_reply
from app.bot.utils.menu_cleanup import cleanup_inline_menu
from app.db.database import AsyncSessionLocal
from app.db.models.lead import LeadStatus
from app.db.repositories.lead_repository import LeadRepository
from app.services.lead_service import LeadService
from app.telegram.safe_sender import TelegramSafeSender

from .lead_callbacks_shared import (
    AmountState,
    NO_ACCESS_TEXT,
    REJECT_REASON_LABELS,
    RejectState,
    _get_group_id,
    _get_manager,
    _manager_can_act,
)

router = Router()


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
            "lead_transition_post_commit_started lead_id={} tenant_id={} transition={} origin=bot_callback",
            lead_id,
            tenant_id,
            transition,
        )
        await service.sync_lead_after_transition(lead_id, transition)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception(
            "lead_transition_post_commit_failed lead_id={} tenant_id={} transition={} origin=bot_callback",
            lead_id,
            tenant_id,
            transition,
        )


@router.callback_query(F.data.regexp(r"^lead:(take|paid|success|reject|reject_reason|clone):"))
async def handle_lead_status_action(
    callback: CallbackQuery,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await sender.answer(callback)
        return

    action = parts[1]
    lead_id_raw = parts[2]
    if not lead_id_raw.isdigit():
        await sender.answer(callback)
        return
    lead_id = int(lead_id_raw)
    source_ref = MessageRef.from_callback(callback)
    group_id = _get_group_id(tenant) or (callback.message.chat.id if callback.message.chat.id < 0 else None)
    if not group_id and callback.message:
        group_id = callback.message.chat.id
    tenant_id = tenant.id if tenant else None

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id, tenant_id=tenant_id)
        if not manager:
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            return

        service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)

        if action == "take":
            lead = await service.take_in_progress(lead_id, callback.from_user.id, source_ref)
            if lead:
                await session.commit()
                await _run_post_commit_sync(
                    session=session,
                    service=service,
                    lead_id=lead.id,
                    tenant_id=tenant_id,
                    transition="take",
                )
                await sender.answer(callback, "\u2705 \u0417\u0430\u044f\u0432\u043a\u0430 \u0432\u0437\u044f\u0442\u0430 \u0432 \u0440\u0430\u0431\u043e\u0442\u0443.")
                return

            existing = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if existing and existing.status != LeadStatus.NEW:
                other_name = existing.manager.name if existing.manager else "\u0434\u0440\u0443\u0433\u043e\u0439 \u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440"
                if existing.manager and existing.manager.tg_id == callback.from_user.id:
                    await sender.answer(callback, "\u26a0\ufe0f \u0412\u044b \u0443\u0436\u0435 \u0432\u0437\u044f\u043b\u0438 \u044d\u0442\u0443 \u0437\u0430\u044f\u0432\u043a\u0443.", show_alert=True)
                else:
                    await sender.answer(callback, f"\u26a0\ufe0f \u0423\u0436\u0435 \u0432\u0437\u044f\u0442\u043e: {other_name}.", show_alert=True)
                return

            await sender.answer(callback, "\u26a0\ufe0f \u0423\u0436\u0435 \u0432\u0437\u044f\u0442\u043e \u0434\u0440\u0443\u0433\u0438\u043c \u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\u043e\u043c.", show_alert=True)
            return

        if action == "paid":
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            await state.set_state(AmountState.waiting_for_amount)
            await state.update_data(lead_id=lead_id, message_ref=source_ref.to_dict() if source_ref else None)
            await sender.answer(callback)
            await start_force_reply(
                callback,
                state,
                sender,
                "\u2139\ufe0f \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0441\u0443\u043c\u043c\u0443 \u0441\u0434\u0435\u043b\u043a\u0438 (\u0440\u0443\u0431.):",
                lead_id=lead_id,
            )
            return

        if action == "success":
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            lead = await service.mark_success(lead_id, callback.from_user.id, source_ref)
            if lead:
                await session.commit()
                await _run_post_commit_sync(
                    session=session,
                    service=service,
                    lead_id=lead.id,
                    tenant_id=tenant_id,
                    transition="success",
                )
                await sender.answer(callback, "\u2705 \u0421\u0434\u0435\u043b\u043a\u0430 \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430.")
                return
            await sender.answer(callback, "\u26a0\ufe0f \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u044c \u0441\u0434\u0435\u043b\u043a\u0443.", show_alert=True)
            return

        if action == "reject":
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            await state.set_state(RejectState.waiting_for_reason)
            await state.update_data(lead_id=lead_id, message_ref=source_ref.to_dict() if source_ref else None)
            await sender.answer(callback)
            await sender.send_ephemeral_text(
                chat_id=callback.message.chat.id,
                message_thread_id=callback.message.message_thread_id,
                text="\u2139\ufe0f \u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043f\u0440\u0438\u0447\u0438\u043d\u0443 \u043e\u0442\u043a\u0430\u0437\u0430:",
                reply_markup=make_reject_reason_keyboard(lead_id),
                ttl_sec=TTL_MENU_SEC,
            )
            return

        if action == "reject_reason":
            if len(parts) < 4:
                await sender.answer(callback)
                return
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            reason_key = parts[3]
            data = await state.get_data()
            target_ref = MessageRef.from_dict(data.get("message_ref"))
            if reason_key == "custom":
                await state.set_state(RejectState.waiting_for_custom_reason)
                await sender.answer(callback)
                await cleanup_inline_menu(callback, sender)
                await start_force_reply(
                    callback,
                    state,
                    sender,
                    "\u2139\ufe0f \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0441\u0432\u043e\u044e \u043f\u0440\u0438\u0447\u0438\u043d\u0443 \u043e\u0442\u043a\u0430\u0437\u0430:",
                    lead_id=lead_id,
                )
                return

            reason = REJECT_REASON_LABELS.get(reason_key)
            if not reason:
                await sender.answer(callback)
                return

            lead = await service.reject_lead(
                lead_id,
                callback.from_user.id,
                reason=reason,
                source_ref=target_ref,
            )
            if lead:
                await session.commit()
                await _run_post_commit_sync(
                    session=session,
                    service=service,
                    lead_id=lead.id,
                    tenant_id=tenant_id,
                    transition="reject",
                )
                await state.clear()
                await sender.answer(callback, "\u2705 \u0413\u043e\u0442\u043e\u0432\u043e.")
                await cleanup_inline_menu(callback, sender)
                return
            await sender.answer(callback, "\u26a0\ufe0f \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c \u0437\u0430\u044f\u0432\u043a\u0443.", show_alert=True)
            return

        if action == "clone":
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            clone = await service.clone_lead(lead_id)
            if clone:
                await session.commit()
                await _run_post_commit_sync(
                    session=session,
                    service=service,
                    lead_id=clone.id,
                    tenant_id=tenant_id,
                    transition="clone",
                )
                await sender.answer(callback, "\u2705 \u041a\u043e\u043f\u0438\u044f \u0441\u043e\u0437\u0434\u0430\u043d\u0430.")
                return
            await sender.answer(callback, "\u26a0\ufe0f \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0437\u0434\u0430\u0442\u044c \u043a\u043e\u043f\u0438\u044e.", show_alert=True)
            return

    logger.warning(f"Unhandled lead action: {action} for lead_id={lead_id}")
