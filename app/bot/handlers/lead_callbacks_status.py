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
                await sender.answer(callback, "✅ Заявка взята в работу.")
                return

            existing = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if existing and existing.status != LeadStatus.NEW:
                other_name = existing.manager.name if existing.manager else "другой менеджер"
                if existing.manager and existing.manager.tg_id == callback.from_user.id:
                    await sender.answer(callback, "⚠️ Вы уже взяли эту заявку.", show_alert=True)
                else:
                    await sender.answer(callback, f"⚠️ Уже взято: {other_name}.", show_alert=True)
                return

            await sender.answer(callback, "⚠️ Уже взято другим менеджером.", show_alert=True)
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
                "ℹ️ Введите сумму сделки (руб.):",
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
                await sender.answer(callback, "✅ Сделка успешно завершена.")
                return
            await sender.answer(callback, "⚠️ Не удалось завершить сделку.", show_alert=True)
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
                text="ℹ️ Укажите причину отказа:",
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
                    "ℹ️ Введите свою причину отказа:",
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
                await state.clear()
                await sender.answer(callback, "✅ Готово.")
                await cleanup_inline_menu(callback, sender)
                return
            await sender.answer(callback, "⚠️ Не удалось отклонить заявку.", show_alert=True)
            return

        if action == "clone":
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            clone = await service.clone_lead(lead_id)
            if clone:
                await session.commit()
                await sender.answer(callback, "✅ Копия создана.")
                return
            await sender.answer(callback, "⚠️ Не удалось создать копию.", show_alert=True)
            return

    logger.warning(f"Unhandled lead action: {action} for lead_id={lead_id}")
