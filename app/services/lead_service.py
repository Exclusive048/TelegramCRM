from loguru import logger
from aiogram.types import InlineKeyboardMarkup

from app.bot.keyboards.lead_keyboards import make_lead_keyboard
from app.bot.topic_resolver import resolve_topic_thread_id
from app.bot.topics import STATUS_TO_TOPIC_KEY, TopicKey
from app.bot.ui.message_ref import MessageRef
from app.bot.ui.tg_edit import archive_message, edit_text
from app.bot.utils.card import format_lead_card, format_archive_card
from app.db.models.lead import Lead, LeadStatus, Manager
from app.db.repositories.lead_repository import LeadRepository
from app.telegram.safe_sender import TelegramSafeSender


class LeadService:
    def __init__(self, repo: LeadRepository, sender: TelegramSafeSender, group_id: int):
        self.repo = repo
        self.sender = sender
        self.group_id = group_id

    async def create_lead(self, data: dict) -> Lead:
        lead = await self.repo.create(data)
        logger.info(f"lead_action=create lead_id={lead.id} source={lead.source}")
        topic_id = None
        if self.group_id:
            topic_id = await resolve_topic_thread_id(
                self.group_id,
                TopicKey.NEW,
                self.repo.session,
                sender=self.sender,
                thread_id=None,
            )
        ref = await self._post_card(lead, topic_id=topic_id) if topic_id else None
        if ref:
            await self.repo.set_active_card_message(
                lead_id=lead.id,
                chat_id=ref.chat_id,
                topic_id=ref.topic_id,
                message_id=ref.message_id,
            )
            logger.info(f"lead_post lead_id={lead.id} ref={ref} ok=True")
        else:
            logger.warning(f"lead_post lead_id={lead.id} ok=False")
        return lead

    async def take_in_progress(
        self,
        lead_id: int,
        manager_tg_id: int,
        source_ref: MessageRef | None,
    ) -> Lead | None:
        manager = await self._get_manager(manager_tg_id)
        if manager_tg_id and not manager:
            return None

        lead = await self.repo.try_take_lead(lead_id, manager.id if manager else None)
        logger.info(
            f"lead_action=take lead_id={lead_id} source_ref={source_ref} db_result={bool(lead)}"
        )
        if not lead:
            return None

        await self._move_card(lead=lead, source_ref=source_ref, action="take")
        return lead

    async def mark_paid(
        self,
        lead_id: int,
        manager_tg_id: int,
        amount: float | None,
        source_ref: MessageRef | None,
    ) -> Lead | None:
        manager = await self._get_manager(manager_tg_id)
        if manager_tg_id and not manager:
            return None

        enforce_manager = not (manager and manager.is_admin)
        lead = await self.repo.mark_paid(
            lead_id,
            manager.id if manager else None,
            amount,
            enforce_manager=enforce_manager,
        )
        logger.info(
            f"lead_action=paid lead_id={lead_id} source_ref={source_ref} db_result={bool(lead)}"
        )
        if not lead:
            return None

        await self._move_card(lead=lead, source_ref=source_ref, action="paid")
        return lead

    async def mark_success(
        self,
        lead_id: int,
        manager_tg_id: int,
        source_ref: MessageRef | None,
    ) -> Lead | None:
        manager = await self._get_manager(manager_tg_id)
        if manager_tg_id and not manager:
            return None

        enforce_manager = not (manager and manager.is_admin)
        lead = await self.repo.mark_success(
            lead_id,
            manager.id if manager else None,
            enforce_manager=enforce_manager,
        )
        logger.info(
            f"lead_action=success lead_id={lead_id} source_ref={source_ref} db_result={bool(lead)}"
        )
        if not lead:
            return None

        await self._move_card(lead=lead, source_ref=source_ref, action="success")
        return lead

    async def reject_lead(
        self,
        lead_id: int,
        manager_tg_id: int,
        reason: str = "",
        source_ref: MessageRef | None = None,
    ) -> Lead | None:
        manager = await self._get_manager(manager_tg_id)
        if manager_tg_id and not manager:
            return None

        enforce_manager = not (manager and manager.is_admin)
        lead = await self.repo.reject_lead(
            lead_id,
            manager.id if manager else None,
            reason=reason or None,
            enforce_manager=enforce_manager,
        )
        logger.info(
            f"lead_action=reject lead_id={lead_id} source_ref={source_ref} db_result={bool(lead)}"
        )
        if not lead:
            return None

        await self._move_card(lead=lead, source_ref=source_ref, action="reject")
        return lead

    async def clone_lead(self, lead_id: int) -> Lead | None:
        lead = await self.repo.get_by_id(lead_id)
        if not lead:
            return None

        data = {
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "source": lead.source,
            "service": lead.service,
            "comment": lead.comment or "",
            "status": LeadStatus.NEW,
        }
        clone = await self.repo.create(data)
        logger.info(f"lead_action=clone source_id={lead_id} clone_id={clone.id}")
        topic_id = None
        if self.group_id:
            topic_id = await resolve_topic_thread_id(
                self.group_id,
                TopicKey.NEW,
                self.repo.session,
                sender=self.sender,
                thread_id=None,
            )
        ref = await self._post_card(clone, topic_id=topic_id) if topic_id else None
        if ref:
            await self.repo.set_active_card_message(
                lead_id=clone.id,
                chat_id=ref.chat_id,
                topic_id=ref.topic_id,
                message_id=ref.message_id,
            )
            logger.info(f"lead_post lead_id={clone.id} ref={ref} ok=True")
        else:
            logger.warning(f"lead_post lead_id={clone.id} ok=False")
        return clone

    async def add_comment(
        self,
        lead_id: int,
        text: str,
        author: str,
        target_ref: MessageRef | None,
    ):
        await self.repo.add_comment(lead_id=lead_id, text=text, author=author)
        lead = await self.repo.get_by_id(lead_id)
        if not lead:
            return

        if target_ref is None:
            target_ref = await self._resolve_active_ref(lead)
        if not target_ref:
            logger.warning(f"lead_comment lead_id={lead_id} target_ref=None")
            return

        active = await self.repo.get_active_card_message(lead_id)
        if active:
            is_active = (
                active.message_id == target_ref.message_id
                and active.chat_id == target_ref.chat_id
                and active.topic_id == target_ref.topic_id
            )
        else:
            is_active = (
                lead.tg_message_id == target_ref.message_id
                and lead.tg_topic_id == target_ref.topic_id
            )

        if is_active and not active:
            await self.repo.set_active_card_message(
                lead_id=lead_id,
                chat_id=target_ref.chat_id,
                topic_id=target_ref.topic_id,
                message_id=target_ref.message_id,
            )
        elif not is_active:
            await self.repo.ensure_card_message(
                lead_id=lead_id,
                chat_id=target_ref.chat_id,
                topic_id=target_ref.topic_id,
                message_id=target_ref.message_id,
                is_active=False,
            )

        text_rendered, keyboard = self._build_card_payload(lead)
        if not is_active:
            keyboard = None

        ok = await edit_text(self.sender, target_ref, text_rendered, keyboard)
        logger.info(
            f"lead_comment lead_id={lead_id} target_ref={target_ref} active={is_active} ok={ok}"
        )

    async def refresh_card(self, lead_id: int):
        lead = await self.repo.get_by_id(lead_id)
        if not lead:
            return

        ref = await self._resolve_active_ref(lead)
        if not ref:
            logger.warning(f"lead_refresh lead_id={lead_id} active_ref=None")
            return

        text_rendered, keyboard = self._build_card_payload(lead)
        ok = await edit_text(self.sender, ref, text_rendered, keyboard)
        logger.info(f"lead_refresh lead_id={lead_id} ref={ref} ok={ok}")

    async def _get_manager(self, manager_tg_id: int) -> Manager | None:
        if not manager_tg_id:
            return None
        return await self.repo.get_manager_by_tg_id(manager_tg_id)

    async def _topic_for_status(self, status: LeadStatus) -> int | None:
        if not self.group_id:
            return None
        key = STATUS_TO_TOPIC_KEY.get(status, TopicKey.NEW)
        return await resolve_topic_thread_id(
            self.group_id,
            key,
            self.repo.session,
            sender=self.sender,
            thread_id=None,
        )

    def _build_card_payload(self, lead: Lead) -> tuple[str, InlineKeyboardMarkup]:
        text = format_lead_card(lead)
        keyboard = make_lead_keyboard(lead.id, lead.status)
        return text, keyboard

    async def _resolve_active_ref(self, lead: Lead) -> MessageRef | None:
        active = await self.repo.get_active_card_message(lead.id)
        if active:
            return MessageRef(
                chat_id=active.chat_id,
                message_id=active.message_id,
                topic_id=active.topic_id,
                source="active_record",
            )
        if not self.group_id:
            return None
        seeded = await self.repo.ensure_active_card_message(
            lead_id=lead.id,
            chat_id=self.group_id,
            topic_id=lead.tg_topic_id,
            message_id=lead.tg_message_id,
        )
        if seeded:
            return MessageRef(
                chat_id=seeded.chat_id,
                message_id=seeded.message_id,
                topic_id=seeded.topic_id,
                source="lead_fallback",
            )
        return None

    async def _post_card(self, lead: Lead, topic_id: int) -> MessageRef | None:
        if not self.group_id:
            return None
        lead_full = await self.repo.get_by_id(lead.id)
        if not lead_full:
            return None
        text, keyboard = self._build_card_payload(lead_full)
        try:
            msg = await self.sender.send_message(
                chat_id=self.group_id,
                message_thread_id=topic_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return MessageRef(
                chat_id=self.group_id,
                message_id=msg.message_id,
                topic_id=topic_id,
                source="post_card",
            )
        except Exception as e:
            logger.error(f"lead_post failed lead_id={lead.id} topic_id={topic_id} err={e}")
            return None

    async def _move_card(
        self,
        lead: Lead,
        source_ref: MessageRef | None,
        *,
        action: str,
    ):
        lead_full = await self.repo.get_by_id(lead.id)
        if not lead_full:
            return

        target_topic = await self._topic_for_status(lead_full.status)
        if not target_topic:
            logger.warning(
                f"lead_move skipped lead_id={lead_full.id} action={action} reason=topic_missing"
            )
            return
        archive_text = format_archive_card(lead_full)

        if source_ref is None:
            source_ref = await self._resolve_active_ref(lead_full)

        archived_ok = False
        if source_ref:
            await self.repo.ensure_card_message(
                lead_id=lead_full.id,
                chat_id=source_ref.chat_id,
                topic_id=source_ref.topic_id,
                message_id=source_ref.message_id,
                is_active=False,
            )
            archived_ok = await archive_message(self.sender, source_ref, archive_text)

        logger.info(
            f"lead_archive lead_id={lead_full.id} action={action} source_ref={source_ref} ok={archived_ok}"
        )

        post_ref = await self._post_card(lead_full, topic_id=target_topic)
        if post_ref:
            await self.repo.set_active_card_message(
                lead_id=lead_full.id,
                chat_id=post_ref.chat_id,
                topic_id=post_ref.topic_id,
                message_id=post_ref.message_id,
            )
            logger.info(
                f"lead_post lead_id={lead_full.id} action={action} target_topic={target_topic} ref={post_ref} ok=True"
            )
        else:
            await self.repo.clear_active_card_message(lead_full.id)
            logger.warning(
                f"lead_post lead_id={lead_full.id} action={action} target_topic={target_topic} ok=False"
            )
