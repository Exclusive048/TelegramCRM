from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from loguru import logger

from app.bot.topic_resolver import resolve_topic_thread_id
from app.bot.topics import TopicKey
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.db.models.lead import LeadStatus
from app.telegram.safe_sender import TelegramSafeSender

import html

_scheduler = AsyncIOScheduler(
    timezone='UTC',
    job_defaults={
        'misfire_grace_time': 3600,
        'coalesce': True,
    }
)
_scheduler_started = False


def _status_label(status: LeadStatus) -> str:
    labels = {
        LeadStatus.NEW: "Лиды",
        LeadStatus.IN_PROGRESS: "В работе",
        LeadStatus.PAID: "Оплачено",
        LeadStatus.SUCCESS: "Успех",
        LeadStatus.REJECTED: "Отклонено",
    }
    return labels.get(status, status.value)


def _build_message_link(chat_id: int, message_id: int) -> str:
    raw = str(chat_id)
    if raw.startswith("-100"):
        chat_part = raw[4:]
    else:
        chat_part = str(abs(chat_id))
    return f"https://t.me/c/{chat_part}/{message_id}"


async def _send_reminder_job(reminder_id: int, sender: TelegramSafeSender):
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        reminder = await repo.get_reminder_by_id(reminder_id)
        if not reminder or reminder.is_sent:
            return
        lead = reminder.lead
        if not lead:
            return

        active = await repo.get_active_card_message(lead.id)
        link = None
        if active:
            link = _build_message_link(active.chat_id, active.message_id)

        lines = [
            f"🔔 Напоминание по заявке #{lead.id}",
            f"👤 {html.escape(lead.name)}",
            f"📱 {html.escape(lead.phone)}",
            f"Статус: {_status_label(lead.status)}",
        ]
        if reminder.message:
            lines.append(f"Комментарий: {html.escape(reminder.message)}")
        if link:
            lines.append(f"Ссылка: {link}")

        topic_id = await resolve_topic_thread_id(
            settings.crm_group_id,
            TopicKey.REMINDERS,
            session,
            sender=sender,
            thread_id=None,
        )
        if not topic_id:
            return

        await sender.send_message(  # FIXED #2
            chat_id=settings.crm_group_id,
            message_thread_id=topic_id,
            text="\n".join(lines),
            parse_mode="HTML",
        )
        await repo.mark_reminder_sent(reminder_id)
        await session.commit()
        logger.info(f"reminder_sent id={reminder_id} lead_id={lead.id}")


class ReminderService:
    def __init__(self, repo: LeadRepository, sender: TelegramSafeSender):
        self.repo = repo
        self.sender = sender

    @staticmethod
    async def start_scheduler(sender: TelegramSafeSender):
        global _scheduler_started
        if not _scheduler_started:
            _scheduler.start()
            _scheduler_started = True
        async with AsyncSessionLocal() as session:
            repo = LeadRepository(session)
            now = datetime.now(timezone.utc)
            reminders = await repo.get_pending_reminders()
            for reminder in reminders:
                _schedule_job(reminder.id, reminder.remind_at, sender, now=now)
            logger.info(f"reminder_scheduler_loaded count={len(reminders)}")

    async def schedule_reminder(
        self,
        lead_id: int,
        manager_tg_id: int,
        remind_at: datetime,
        message: str | None = None,
    ):
        reminder = await self.repo.create_reminder(
            lead_id=lead_id,
            manager_tg_id=manager_tg_id,
            remind_at=remind_at,
            message=message,
        )
        _schedule_job(reminder.id, remind_at, self.sender)
        logger.info(
            f"reminder_created id={reminder.id} lead_id={lead_id} remind_at={remind_at.isoformat()}"
        )
        return reminder


def _schedule_job(reminder_id: int, remind_at: datetime, sender: TelegramSafeSender, *, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    run_at = remind_at if remind_at > now else now + timedelta(seconds=1)
    job_id = f"reminder:{reminder_id}"
    trigger = DateTrigger(run_date=run_at)
    _scheduler.add_job(
        _send_reminder_job,
        trigger=trigger,
        args=[reminder_id, sender],
        id=job_id,
        replace_existing=True,
    )
