from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from loguru import logger

from app.bot.topic_resolver import resolve_topic_thread_id
from app.bot.topics import TopicKey
from app.db.database import AsyncSessionLocal
from app.db.models.lead import LeadStatus
from app.db.repositories.lead_repository import LeadRepository
from app.services.lead_service import LeadService
from app.telegram.safe_sender import TelegramSafeSender

_scheduler = AsyncIOScheduler(
    timezone="UTC",
    job_defaults={
        "misfire_grace_time": 3600,
        "coalesce": True,
    },
)
_scheduler_started = False


def _job_id(reminder_id: int) -> str:
    return f"reminder:{reminder_id}"


def _unschedule_job(reminder_id: int) -> None:
    try:
        _scheduler.remove_job(_job_id(reminder_id))
    except Exception:
        pass


async def _refresh_lead_card(
    repo: LeadRepository,
    sender: TelegramSafeSender,
    lead_id: int,
    group_id: int | None,
) -> None:
    try:
        lead = await repo.get_by_id(lead_id)
        if not lead:
            return
        card_group_id = group_id or await repo.get_group_id_for_lead(lead_id) or 0
        service = LeadService(
            repo,
            sender,
            group_id=card_group_id,
            tenant_id=lead.tenant_id,
        )
        await service.refresh_card(lead_id)
    except Exception as exc:
        logger.warning(f"reminder_refresh_card_failed lead_id={lead_id} err={exc}")


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


async def _reschedule_after_failure(
    repo: LeadRepository,
    sender: TelegramSafeSender,
    reminder_id: int,
    lead_id: int,
    group_id: int | None,
) -> None:
    retry_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await repo.release_reminder_after_failure(reminder_id, retry_at=retry_at)
    await _refresh_lead_card(repo, sender, lead_id, group_id)
    _schedule_job(reminder_id, retry_at, sender, group_id=group_id)


async def _send_reminder_job(reminder_id: int, sender: TelegramSafeSender, group_id: int | None):
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        claimed = await repo.claim_reminder_for_delivery(reminder_id)
        if not claimed:
            return
        await session.commit()

        reminder = await repo.get_reminder_by_id(reminder_id)
        if not reminder or reminder.is_sent:
            return
        lead = reminder.lead
        if not lead:
            await repo.cancel_reminder(reminder_id)
            await session.commit()
            return

        if group_id is None:
            group_id = await repo.get_group_id_for_lead(lead.id)
        if not group_id:
            await _reschedule_after_failure(repo, sender, reminder_id, lead.id, group_id)
            await session.commit()
            return

        active = await repo.get_active_card_message(lead.id)
        link = _build_message_link(active.chat_id, active.message_id) if active else None

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
            group_id,
            TopicKey.REMINDERS,
            session,
            sender=sender,
            thread_id=None,
        )
        if not topic_id:
            await _reschedule_after_failure(repo, sender, reminder_id, lead.id, group_id)
            await session.commit()
            return

        try:
            await sender.send_message(
                chat_id=group_id,
                message_thread_id=topic_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )
        except Exception as exc:
            await _reschedule_after_failure(repo, sender, reminder_id, lead.id, group_id)
            await session.commit()
            logger.warning(
                "reminder_send_failed id={} lead_id={} err={}",
                reminder_id,
                lead.id,
                exc,
            )
            return

        await repo.mark_reminder_sent(reminder_id)
        await _refresh_lead_card(repo, sender, lead.id, group_id)
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
            due_reminders = await repo.get_pending_reminders(due_before_now=True, stale_after_seconds=0)
            for reminder in due_reminders:
                group_id = await repo.get_group_id_for_lead(reminder.lead_id)
                _schedule_job(
                    reminder.id,
                    reminder.remind_at,
                    sender,
                    group_id=group_id,
                    now=now,
                    immediate=True,
                )

            future_reminders = await repo.get_pending_reminders(stale_after_seconds=0)
            for reminder in future_reminders:
                group_id = await repo.get_group_id_for_lead(reminder.lead_id)
                _schedule_job(reminder.id, reminder.remind_at, sender, group_id=group_id, now=now)

            logger.info(
                "reminder_scheduler_loaded overdue_count={} future_count={} total={}",
                len(due_reminders),
                len(future_reminders),
                len(due_reminders) + len(future_reminders),
            )

    async def schedule_reminder(
        self,
        lead_id: int,
        manager_tg_id: int,
        remind_at: datetime,
        group_id: int | None,
        message: str | None = None,
    ):
        reminder = await self.repo.create_reminder(
            lead_id=lead_id,
            manager_tg_id=manager_tg_id,
            remind_at=remind_at,
            message=message,
        )
        _unschedule_job(reminder.id)
        _schedule_job(reminder.id, remind_at, self.sender, group_id=group_id)
        await _refresh_lead_card(self.repo, self.sender, lead_id, group_id)
        logger.info(
            f"reminder_created id={reminder.id} lead_id={lead_id} remind_at={remind_at.isoformat()}"
        )
        return reminder

    async def replace_reminder(
        self,
        lead_id: int,
        manager_tg_id: int,
        remind_at: datetime,
        group_id: int | None,
        message: str | None = None,
    ):
        existing = await self.repo.get_active_reminder_for_manager(lead_id, manager_tg_id)
        if existing:
            await self.repo.cancel_reminder(existing.id)
            _unschedule_job(existing.id)

        reminder = await self.repo.create_reminder(
            lead_id=lead_id,
            manager_tg_id=manager_tg_id,
            remind_at=remind_at,
            message=message,
        )
        _schedule_job(reminder.id, remind_at, self.sender, group_id=group_id)
        await _refresh_lead_card(self.repo, self.sender, lead_id, group_id)
        logger.info(
            "reminder_replaced old_id={} new_id={} lead_id={} remind_at={}",
            existing.id if existing else None,
            reminder.id,
            lead_id,
            remind_at.isoformat(),
        )
        return reminder, existing


def _schedule_job(
    reminder_id: int,
    remind_at: datetime,
    sender: TelegramSafeSender,
    *,
    group_id: int | None,
    now: datetime | None = None,
    immediate: bool = False,
):
    def _ensure_aware(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    remind_at = _ensure_aware(remind_at)
    now = _ensure_aware(now or datetime.now(timezone.utc))
    run_at = now if immediate else (remind_at if remind_at > now else now + timedelta(seconds=1))

    _scheduler.add_job(
        _send_reminder_job,
        trigger=DateTrigger(run_date=run_at),
        args=[reminder_id, sender, group_id],
        id=_job_id(reminder_id),
        replace_existing=True,
    )
