from loguru import logger
from aiogram import Bot
from app.db.repositories.lead_repository import LeadRepository
from app.db.models.lead import Lead, LeadStatus
from app.core.config import settings
from app.bot.keyboards.lead_keyboards import make_lead_keyboard
from app.bot.utils.card import format_lead_card


class LeadService:

    def __init__(self, repo: LeadRepository, bot: Bot):
        self.repo = repo
        self.bot = bot

    # ── Создание ──────────────────────────────────────

    async def create_lead(self, data: dict) -> Lead:
        lead = await self.repo.create(data)
        logger.info(f"Lead #{lead.id} created from {lead.source}")
        await self._post_card(lead, topic_id=settings.topic_new)
        return lead

    # ── Переходы статусов ─────────────────────────────

    async def take_in_progress(self, lead_id: int, manager_tg_id: int) -> Lead | None:
        manager = await self.repo.get_manager_by_tg_id(manager_tg_id)
        if not manager:
            return None
        lead = await self.repo.update_status(
            lead_id, LeadStatus.IN_PROGRESS,
            manager_id=manager.id,
            comment=f"Взял в работу: {manager.name}",
        )
        if not lead:
            return None
        await self._archive_card(lead, label=f"🔵 Взят в обработку — {manager.name}")
        await self._post_card(lead, topic_id=settings.topic_in_progress)
        return lead

    async def close_lead(self, lead_id: int, manager_tg_id: int) -> Lead | None:
        manager = await self.repo.get_manager_by_tg_id(manager_tg_id)
        lead = await self.repo.update_status(
            lead_id, LeadStatus.CLOSED,
            manager_id=manager.id if manager else None,
            comment="Сделка закрыта",
        )
        if not lead:
            return None
        name = manager.name if manager else "—"
        await self._archive_card(lead, label=f"✅ Закрыт — {name}")
        await self._post_card(lead, topic_id=settings.topic_closed)
        return lead

    async def reject_lead(self, lead_id: int, manager_tg_id: int, reason: str = "") -> Lead | None:
        manager = await self.repo.get_manager_by_tg_id(manager_tg_id)
        lead = await self.repo.update_status(
            lead_id, LeadStatus.REJECTED,
            manager_id=manager.id if manager else None,
            reject_reason=reason,
            comment=f"Отклонено: {reason}" if reason else "Отклонено",
        )
        if not lead:
            return None
        name = manager.name if manager else "—"
        note = f"\nПричина: {reason}" if reason else ""
        await self._archive_card(lead, label=f"❌ Отклонён — {name}{note}")
        await self._post_card(lead, topic_id=settings.topic_rejected)
        return lead

    # ── Telegram карточки ─────────────────────────────

    async def _post_card(self, lead: Lead, topic_id: int):
        """Отправить карточку в топик, сохранить message_id"""
        # Перечитываем с комментариями
        lead = await self.repo.get_by_id(lead.id)
        text = format_lead_card(lead, show_comments=True)
        keyboard = make_lead_keyboard(lead.id, lead.status)
        try:
            msg = await self.bot.send_message(
                chat_id=settings.crm_group_id,
                message_thread_id=topic_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            await self.repo.set_tg_message(lead.id, msg.message_id, topic_id)
        except Exception as e:
            logger.error(f"Failed to post lead #{lead.id} to TG topic {topic_id}: {e}")

    async def _archive_card(self, lead: Lead, label: str):
        """
        Редактирует старую карточку: убирает кнопки, добавляет статус.
        Фикс: явно передаём reply_markup=None чтобы убрать кнопки.
        """
        if not lead.tg_message_id or not lead.tg_topic_id:
            return
        lead_full = await self.repo.get_by_id(lead.id)
        archived_text = format_lead_card(lead_full, show_comments=True) + f"\n\n<i>{label}</i>"
        try:
            await self.bot.edit_message_text(
                chat_id=settings.crm_group_id,
                message_id=lead.tg_message_id,
                text=archived_text,
                reply_markup=None,   # ← убираем кнопки
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Could not archive card for lead #{lead.id}: {e}")

    async def refresh_card(self, lead_id: int):
        """Перерисовать карточку в текущем топике (например, после добавления заметки)"""
        lead = await self.repo.get_by_id(lead_id)
        if not lead or not lead.tg_message_id:
            return
        text = format_lead_card(lead, show_comments=True)
        keyboard = make_lead_keyboard(lead.id, lead.status)
        try:
            await self.bot.edit_message_text(
                chat_id=settings.crm_group_id,
                message_id=lead.tg_message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Could not refresh card for lead #{lead_id}: {e}")
