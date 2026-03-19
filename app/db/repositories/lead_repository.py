from datetime import datetime, timedelta, timezone
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from app.db.models.lead import (
    Lead,
    LeadArchive,
    LeadStatus,
    LeadHistory,
    LeadComment,
    Manager,
    ManagerRole,
    PanelMessage,
    LeadCardMessage,
    Reminder,
)
from app.db.models.tenant import Tenant
from app.db.utils import _naive




class LeadRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    _FINAL_ARCHIVE_STATUSES = frozenset({LeadStatus.SUCCESS, LeadStatus.REJECTED})

    @staticmethod
    def _require_tenant_scope(tenant_id: int | None, *, operation: str) -> int:
        if tenant_id is None:
            raise ValueError(
                f"{operation} requires tenant_id to avoid cross-tenant fail-open access"
            )
        return tenant_id

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        return _naive(value).isoformat(sep=" ")

    @staticmethod
    def _status_to_value(status: LeadStatus | str | None) -> str | None:
        if status is None:
            return None
        if isinstance(status, LeadStatus):
            return status.value
        return str(status)

    # ── Создание ──────────────────────────────────────

    async def create(self, data: dict) -> Lead:
        status = data.get("status")
        if isinstance(status, str):
            data["status"] = LeadStatus(status.lower())
        lead = Lead(**data)
        self.session.add(lead)
        await self.session.flush()
        return lead

    # ── Получение ─────────────────────────────────────

    async def get_by_id(self, lead_id: int, tenant_id: int | None = None) -> Lead | None:
        query = (
            select(Lead)
            .options(selectinload(Lead.manager), selectinload(Lead.history), selectinload(Lead.comments))
            .where(Lead.id == lead_id)
        )
        if tenant_id is not None:
            query = query.where(Lead.tenant_id == tenant_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_id_scoped(self, lead_id: int, tenant_id: int | None) -> Lead | None:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="get_by_id_scoped",
        )
        return await self.get_by_id(lead_id, tenant_id=scoped_tenant_id)

    async def get_list(
        self,
        status: LeadStatus | None = None,
        source: str | None = None,
        manager_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 50,
        tenant_id: int | None = None,
    ) -> tuple[list[Lead], int]:
        query = select(Lead).options(selectinload(Lead.manager))
        if tenant_id is not None:
            query = query.where(Lead.tenant_id == tenant_id)
        if status:
            query = query.where(Lead.status == status)
        if source:
            query = query.where(Lead.source == source)
        if manager_id:
            query = query.where(Lead.manager_id == manager_id)
        date_from, date_to = _naive(date_from), _naive(date_to)
        if date_from:
            query = query.where(Lead.created_at >= date_from)
        if date_to:
            query = query.where(Lead.created_at <= date_to)
        if search:
            query = query.where(Lead.name.ilike(f"%{search}%") | Lead.phone.ilike(f"%{search}%"))

        count_result = await self.session.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar()

        query = query.order_by(Lead.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        result = await self.session.execute(query)
        return result.scalars().all(), total

    async def get_list_scoped(
        self,
        status: LeadStatus | None = None,
        source: str | None = None,
        manager_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 50,
        tenant_id: int | None = None,
    ) -> tuple[list[Lead], int]:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="get_list_scoped",
        )
        return await self.get_list(
            status=status,
            source=source,
            manager_id=manager_id,
            date_from=date_from,
            date_to=date_to,
            search=search,
            page=page,
            per_page=per_page,
            tenant_id=scoped_tenant_id,
        )

    # ── Обновление ────────────────────────────────────

    def _build_archive_status_history(self, lead: Lead) -> list[dict[str, int | str | None]]:
        events: list[dict[str, int | str | None]] = [
            {
                "from_status": None,
                "to_status": self._status_to_value(LeadStatus.NEW),
                "manager_id": lead.manager_id,
                "comment": "lead_created",
                "created_at": self._serialize_datetime(lead.created_at),
            }
        ]
        ordered_history = sorted(
            lead.history,
            key=lambda item: item.created_at or datetime.min,
        )
        for item in ordered_history:
            events.append(
                {
                    "from_status": self._status_to_value(item.from_status),
                    "to_status": self._status_to_value(item.to_status),
                    "manager_id": item.manager_id,
                    "comment": item.comment,
                    "created_at": self._serialize_datetime(item.created_at),
                }
            )
        return events

    def _build_archive_snapshot(
        self,
        lead: Lead,
        *,
        status_history: list[dict[str, int | str | None]],
        tg_chat_id: int | None,
    ) -> dict:
        return {
            "lead_id": lead.id,
            "tenant_id": lead.tenant_id,
            "status": self._status_to_value(lead.status),
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "source": lead.source,
            "service": lead.service,
            "comment": lead.comment or "",
            "amount": float(lead.amount) if lead.amount is not None else None,
            "manager_id": lead.manager_id,
            "reject_reason": lead.reject_reason,
            "utm_campaign": lead.utm_campaign,
            "utm_source": lead.utm_source,
            "extra": lead.extra,
            "tg_chat_id": tg_chat_id,
            "tg_topic_id": lead.tg_topic_id,
            "tg_message_id": lead.tg_message_id,
            "created_at": self._serialize_datetime(lead.created_at),
            "closed_at": self._serialize_datetime(lead.closed_at),
            "history": status_history,
        }

    async def _resolve_archive_chat_context(self, lead_id: int) -> int | None:
        context_result = await self.session.execute(
            select(LeadCardMessage.chat_id)
            .where(LeadCardMessage.lead_id == lead_id)
            .order_by(
                LeadCardMessage.is_active.desc(),
                LeadCardMessage.created_at.desc(),
            )
            .limit(1)
        )
        return context_result.scalar_one_or_none()

    async def archive_lead_snapshot_if_final(
        self,
        lead_id: int,
        *,
        tenant_id: int | None = None,
    ) -> bool:
        lead = await self.get_by_id(lead_id, tenant_id=tenant_id)
        if not lead:
            return False
        if lead.status not in self._FINAL_ARCHIVE_STATUSES:
            return False

        try:
            async with self.session.begin_nested():
                status_history = self._build_archive_status_history(lead)
                tg_chat_id = await self._resolve_archive_chat_context(lead.id)
                existing_result = await self.session.execute(
                    select(LeadArchive).where(LeadArchive.source_lead_id == lead.id)
                )
                archive = existing_result.scalar_one_or_none()
                if archive is None:
                    archive = LeadArchive(
                        source_lead_id=lead.id,
                        tenant_id=lead.tenant_id,
                        tg_chat_id=tg_chat_id,
                        tg_topic_id=lead.tg_topic_id,
                        tg_message_id=lead.tg_message_id,
                        name=lead.name,
                        phone=lead.phone,
                        email=lead.email,
                        source=lead.source,
                        service=lead.service,
                        comment=lead.comment or "",
                        amount=lead.amount,
                        manager_id=lead.manager_id,
                        reject_reason=lead.reject_reason,
                        utm_campaign=lead.utm_campaign,
                        utm_source=lead.utm_source,
                        extra=lead.extra,
                        lead_created_at=_naive(lead.created_at),
                        lead_closed_at=_naive(lead.closed_at),
                        final_status=lead.status,
                        status_history=status_history,
                        snapshot=self._build_archive_snapshot(
                            lead,
                            status_history=status_history,
                            tg_chat_id=tg_chat_id,
                        ),
                    )
                    self.session.add(archive)
                else:
                    archive.tenant_id = lead.tenant_id
                    archive.tg_chat_id = tg_chat_id
                    archive.tg_topic_id = lead.tg_topic_id
                    archive.tg_message_id = lead.tg_message_id
                    archive.name = lead.name
                    archive.phone = lead.phone
                    archive.email = lead.email
                    archive.source = lead.source
                    archive.service = lead.service
                    archive.comment = lead.comment or ""
                    archive.amount = lead.amount
                    archive.manager_id = lead.manager_id
                    archive.reject_reason = lead.reject_reason
                    archive.utm_campaign = lead.utm_campaign
                    archive.utm_source = lead.utm_source
                    archive.extra = lead.extra
                    archive.lead_created_at = _naive(lead.created_at)
                    archive.lead_closed_at = _naive(lead.closed_at)
                    archive.final_status = lead.status
                    archive.status_history = status_history
                    archive.snapshot = self._build_archive_snapshot(
                        lead,
                        status_history=status_history,
                        tg_chat_id=tg_chat_id,
                    )
                await self.session.flush()
            logger.info(
                "lead_archive_upserted lead_id={} tenant_id={} final_status={} history_events={}",
                lead.id,
                lead.tenant_id,
                self._status_to_value(lead.status),
                len(status_history),
            )
            return True
        except Exception:
            logger.exception(
                "lead_archive_upsert_failed lead_id={} tenant_id={} final_status={}",
                lead.id,
                lead.tenant_id,
                self._status_to_value(lead.status),
            )
            return False

    async def archive_lead_snapshot_if_final_scoped(
        self,
        lead_id: int,
        *,
        tenant_id: int | None,
    ) -> bool:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="archive_lead_snapshot_if_final_scoped",
        )
        return await self.archive_lead_snapshot_if_final(
            lead_id,
            tenant_id=scoped_tenant_id,
        )

    async def get_archive_report(
        self,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        per_page: int = 1000,
        tenant_id: int | None = None,
    ) -> tuple[list[LeadArchive], int]:
        period_from, period_to = _naive(date_from), _naive(date_to)
        period_column = func.coalesce(LeadArchive.lead_closed_at, LeadArchive.archived_at)
        query = select(LeadArchive)
        if tenant_id is not None:
            query = query.where(LeadArchive.tenant_id == tenant_id)
        if period_from:
            query = query.where(period_column >= period_from)
        if period_to:
            query = query.where(period_column <= period_to)

        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = int(count_result.scalar() or 0)

        rows_result = await self.session.execute(
            query.order_by(LeadArchive.lead_closed_at.desc(), LeadArchive.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return rows_result.scalars().all(), total

    async def get_archive_report_scoped(
        self,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        per_page: int = 1000,
        tenant_id: int | None,
    ) -> tuple[list[LeadArchive], int]:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="get_archive_report_scoped",
        )
        return await self.get_archive_report(
            date_from=date_from,
            date_to=date_to,
            page=page,
            per_page=per_page,
            tenant_id=scoped_tenant_id,
        )

    async def get_archive_status_analytics(
        self,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        tenant_id: int | None = None,
    ) -> dict[str, int]:
        period_from, period_to = _naive(date_from), _naive(date_to)
        period_column = func.coalesce(LeadArchive.lead_closed_at, LeadArchive.archived_at)
        query = select(LeadArchive.final_status, func.count()).select_from(LeadArchive)
        if tenant_id is not None:
            query = query.where(LeadArchive.tenant_id == tenant_id)
        if period_from:
            query = query.where(period_column >= period_from)
        if period_to:
            query = query.where(period_column <= period_to)
        query = query.group_by(LeadArchive.final_status)

        rows = (await self.session.execute(query)).all()
        by_status = {
            LeadStatus.SUCCESS.value: 0,
            LeadStatus.REJECTED.value: 0,
        }
        for status, count in rows:
            key = self._status_to_value(status)
            if key is None:
                continue
            by_status[key] = int(count)
        return by_status

    async def get_archive_status_analytics_scoped(
        self,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        tenant_id: int | None,
    ) -> dict[str, int]:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="get_archive_status_analytics_scoped",
        )
        return await self.get_archive_status_analytics(
            date_from=date_from,
            date_to=date_to,
            tenant_id=scoped_tenant_id,
        )

    async def update_status(
        self,
        lead_id: int,
        new_status: LeadStatus,
        manager_id: int | None = None,
        comment: str | None = None,
        reject_reason: str | None = None,
        tenant_id: int | None = None,
    ) -> Lead | None:
        lead = await self.get_by_id(lead_id, tenant_id=tenant_id)
        if not lead:
            return None
        old_status = lead.status
        lead.status = new_status
        if manager_id:
            lead.manager_id = manager_id
        if reject_reason:
            lead.reject_reason = reject_reason
        if new_status in (LeadStatus.SUCCESS, LeadStatus.REJECTED):
            lead.closed_at = _naive(datetime.now(timezone.utc))

        self.session.add(LeadHistory(
            lead_id=lead_id,
            from_status=old_status,
            to_status=new_status,
            manager_id=manager_id,
            comment=comment,
        ))
        await self.session.flush()
        if new_status in self._FINAL_ARCHIVE_STATUSES:
            await self.archive_lead_snapshot_if_final(
                lead_id,
                tenant_id=lead.tenant_id,
            )
        return lead

    async def try_take_lead(self, lead_id: int, manager_id: int | None) -> Lead | None:
        result = await self.session.execute(
            update(Lead)
            .where(Lead.id == lead_id, Lead.status == LeadStatus.NEW)
            .values(status=LeadStatus.IN_PROGRESS, manager_id=manager_id, updated_at=func.now())
            .returning(Lead.id)
        )
        updated_id = result.scalar_one_or_none()
        if not updated_id:
            return None

        self.session.add(LeadHistory(
            lead_id=lead_id,
            from_status=LeadStatus.NEW,
            to_status=LeadStatus.IN_PROGRESS,
            manager_id=manager_id,
            comment="Взял в работу",
        ))
        await self.session.flush()
        return await self.get_by_id(lead_id)

    async def mark_paid(
        self,
        lead_id: int,
        manager_id: int | None,
        amount: float | None,
        *,
        enforce_manager: bool = True,
    ) -> Lead | None:
        stmt = update(Lead).where(
            Lead.id == lead_id,
            Lead.status == LeadStatus.IN_PROGRESS,
        )
        if enforce_manager and manager_id:
            stmt = stmt.where(Lead.manager_id == manager_id)

        values: dict = {"status": LeadStatus.PAID, "amount": amount}
        if manager_id:
            values["manager_id"] = manager_id

        result = await self.session.execute(
            stmt.values(**values).returning(Lead.id)
        )
        updated_id = result.scalar_one_or_none()
        if not updated_id:
            return None

        self.session.add(LeadHistory(
            lead_id=lead_id,
            from_status=LeadStatus.IN_PROGRESS,
            to_status=LeadStatus.PAID,
            manager_id=manager_id,
            comment="\u041e\u043f\u043b\u0430\u0447\u0435\u043d\u043e",
        ))
        await self.session.flush()
        return await self.get_by_id(lead_id)

    async def mark_success(
        self,
        lead_id: int,
        manager_id: int | None,
        *,
        enforce_manager: bool = True,
    ) -> Lead | None:
        stmt = update(Lead).where(
            Lead.id == lead_id,
            Lead.status == LeadStatus.PAID,
        )
        if enforce_manager and manager_id:
            stmt = stmt.where(Lead.manager_id == manager_id)

        values: dict = {"status": LeadStatus.SUCCESS, "closed_at": func.now()}
        if manager_id:
            values["manager_id"] = manager_id

        result = await self.session.execute(
            stmt.values(**values).returning(Lead.id)
        )
        updated_id = result.scalar_one_or_none()
        if not updated_id:
            return None

        self.session.add(LeadHistory(
            lead_id=lead_id,
            from_status=LeadStatus.PAID,
            to_status=LeadStatus.SUCCESS,
            manager_id=manager_id,
            comment="\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e",
        ))
        await self.session.flush()
        await self.archive_lead_snapshot_if_final(updated_id)
        return await self.get_by_id(lead_id)

    async def reject_lead(
        self,
        lead_id: int,
        manager_id: int | None,
        reason: str | None = None,
        *,
        enforce_manager: bool = True,
    ) -> Lead | None:
        current_status_result = await self.session.execute(
            select(Lead.status).where(Lead.id == lead_id)
        )
        from_status = current_status_result.scalar_one_or_none()

        stmt = update(Lead).where(
            Lead.id == lead_id,
            Lead.status.in_([LeadStatus.NEW, LeadStatus.IN_PROGRESS, LeadStatus.PAID]),
        )
        if enforce_manager and manager_id:
            stmt = stmt.where(
                (Lead.manager_id == manager_id) | (Lead.manager_id.is_(None))
            )

        values = {"status": LeadStatus.REJECTED, "reject_reason": reason, "closed_at": func.now()}
        if manager_id:
            values["manager_id"] = manager_id

        result = await self.session.execute(
            stmt.values(**values).returning(Lead.id)
        )
        updated_id = result.scalar_one_or_none()
        if not updated_id:
            return None

        comment = f"Отклонено: {reason}" if reason else "Отклонено"
        self.session.add(LeadHistory(
            lead_id=lead_id,
            from_status=from_status,
            to_status=LeadStatus.REJECTED,
            manager_id=manager_id,
            comment=comment,
        ))
        await self.session.flush()
        await self.archive_lead_snapshot_if_final(updated_id)
        return await self.get_by_id(lead_id)

    async def set_tg_message(self, lead_id: int, message_id: int | None, topic_id: int | None):
        await self.session.execute(
            update(Lead).where(Lead.id == lead_id)
            .values(tg_message_id=message_id, tg_topic_id=topic_id, updated_at=func.now())
        )

    # ── Lead card messages ───────────────────────────────────────────────────

    async def get_card_message(
        self,
        chat_id: int,
        message_id: int,
        tenant_id: int | None = None,
    ) -> LeadCardMessage | None:
        query = select(LeadCardMessage).where(
            LeadCardMessage.chat_id == chat_id,
            LeadCardMessage.message_id == message_id,
        )
        if tenant_id is not None:
            query = query.join(Lead, Lead.id == LeadCardMessage.lead_id).where(Lead.tenant_id == tenant_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_active_card_message(self, lead_id: int) -> LeadCardMessage | None:
        result = await self.session.execute(
            select(LeadCardMessage)
            .where(LeadCardMessage.lead_id == lead_id, LeadCardMessage.is_active == True)
            .order_by(LeadCardMessage.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def ensure_card_message(
        self,
        lead_id: int,
        chat_id: int,
        topic_id: int | None,
        message_id: int,
        *,
        is_active: bool = False,
    ) -> LeadCardMessage:
        existing = await self.get_card_message(chat_id, message_id)
        if existing:
            return existing

        record = LeadCardMessage(
            lead_id=lead_id,
            chat_id=chat_id,
            topic_id=topic_id,
            message_id=message_id,
            is_active=is_active,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def ensure_active_card_message(
        self,
        lead_id: int,
        chat_id: int,
        topic_id: int | None,
        message_id: int | None,
    ) -> LeadCardMessage | None:
        existing_active = await self.get_active_card_message(lead_id)
        if existing_active:
            return existing_active
        if message_id is None:
            return None
        existing = await self.get_card_message(chat_id, message_id)
        if existing:
            return existing if existing.is_active else None
        record = LeadCardMessage(
            lead_id=lead_id,
            chat_id=chat_id,
            topic_id=topic_id,
            message_id=message_id,
            is_active=True,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def set_active_card_message(
        self,
        lead_id: int,
        chat_id: int,
        topic_id: int | None,
        message_id: int,
    ) -> LeadCardMessage:
        await self.session.execute(
            update(LeadCardMessage)
            .where(LeadCardMessage.lead_id == lead_id, LeadCardMessage.is_active == True)
            .values(is_active=False)
        )
        record = LeadCardMessage(
            lead_id=lead_id,
            chat_id=chat_id,
            topic_id=topic_id,
            message_id=message_id,
            is_active=True,
        )
        self.session.add(record)
        await self.session.flush()
        await self.set_tg_message(lead_id, message_id, topic_id)
        return record

    async def clear_active_card_message(self, lead_id: int):
        await self.session.execute(
            update(LeadCardMessage)
            .where(LeadCardMessage.lead_id == lead_id, LeadCardMessage.is_active == True)
            .values(is_active=False)
        )
        await self.set_tg_message(lead_id, None, None)

    async def get_lead_by_tg_message(
        self,
        message_id: int,
        topic_id: int | None,
    ) -> Lead | None:
        result = await self.session.execute(
            select(Lead).where(
                Lead.tg_message_id == message_id,
                Lead.tg_topic_id == topic_id,
            )
        )
        return result.scalar_one_or_none()

    # ── Panel message storage ─────────────────────────────────────────────────

    async def get_panel_message(self, chat_id: int, topic_id: int) -> PanelMessage | None:
        result = await self.session.execute(
            select(PanelMessage).where(
                PanelMessage.chat_id == chat_id,
                PanelMessage.topic_id == topic_id,
            )
        )
        return result.scalar_one_or_none()

    async def set_panel_message_id(self, chat_id: int, topic_id: int, message_id: int) -> PanelMessage:
        existing = await self.get_panel_message(chat_id, topic_id)
        if existing:
            existing.message_id = message_id
            await self.session.flush()
            return existing

        record = PanelMessage(
            chat_id=chat_id,
            topic_id=topic_id,
            message_id=message_id,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_or_create_panel_message_id(
        self,
        chat_id: int,
        topic_id: int,
        message_id: int | None = None,
    ) -> int | None:
        existing = await self.get_panel_message(chat_id, topic_id)
        if existing:
            return existing.message_id
        if message_id is None:
            return None
        await self.set_panel_message_id(chat_id, topic_id, message_id)
        return message_id

    # ── Комментарии ───────────────────────────────────

    async def add_comment(
        self,
        lead_id: int,
        text: str,
        author: str,
        tenant_id: int | None = None,
    ) -> LeadComment | None:
        if tenant_id is not None:
            lead = await self.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead:
                return None
        comment = LeadComment(lead_id=lead_id, text=text, author=author)
        self.session.add(comment)
        await self.session.flush()
        return comment

    # Reminders

    async def create_reminder(
        self,
        lead_id: int,
        manager_tg_id: int,
        remind_at: datetime,
        message: str | None = None,
    ) -> Reminder:
        reminder = Reminder(
            lead_id=lead_id,
            manager_tg_id=manager_tg_id,
            remind_at=_naive(remind_at),
            message=message,
            is_sent=False,
            is_processing=False,
            processing_started_at=None,
            retry_count=0,
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def get_pending_reminders(
        self,
        *,
        due_before_now: bool = False,
        stale_after_seconds: int = 300,
    ) -> list[Reminder]:
        now = datetime.now(timezone.utc)
        stale_before = _naive(now) - timedelta(seconds=stale_after_seconds)
        query = select(Reminder).where(
            Reminder.is_sent == False,
            (
                (Reminder.is_processing == False)
                | (Reminder.processing_started_at.is_(None))
                | (Reminder.processing_started_at <= stale_before)
            ),
        )
        if due_before_now:
            query = query.where(Reminder.remind_at <= now)
        else:
            query = query.where(Reminder.remind_at > now)
        result = await self.session.execute(
            query.order_by(Reminder.remind_at.asc())
        )
        return result.scalars().all()

    async def get_pending_reminders_with_group_id(
        self,
        *,
        due_before_now: bool = False,
        stale_after_seconds: int = 300,
    ) -> list[tuple[Reminder, int | None]]:
        now = datetime.now(timezone.utc)
        stale_before = _naive(now) - timedelta(seconds=stale_after_seconds)
        query = (
            select(Reminder, Tenant.group_id)
            .join(Lead, Lead.id == Reminder.lead_id)
            .outerjoin(Tenant, Tenant.id == Lead.tenant_id)
            .where(
                Reminder.is_sent == False,
                (
                    (Reminder.is_processing == False)
                    | (Reminder.processing_started_at.is_(None))
                    | (Reminder.processing_started_at <= stale_before)
                ),
            )
        )
        if due_before_now:
            query = query.where(Reminder.remind_at <= now)
        else:
            query = query.where(Reminder.remind_at > now)
        result = await self.session.execute(
            query.order_by(Reminder.remind_at.asc())
        )
        rows = result.all()
        return [(reminder, group_id) for reminder, group_id in rows]

    async def get_active_reminder(self, lead_id: int) -> Reminder | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.lead_id == lead_id,
                Reminder.is_sent == False,
                Reminder.remind_at >= now,
            )
            .order_by(Reminder.remind_at.asc(), Reminder.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_reminder_for_manager(
        self,
        lead_id: int,
        manager_tg_id: int,
    ) -> Reminder | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.lead_id == lead_id,
                Reminder.manager_tg_id == manager_tg_id,
                Reminder.is_sent == False,
                Reminder.remind_at >= now,
            )
            .order_by(Reminder.remind_at.asc(), Reminder.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_reminder_by_id(self, reminder_id: int) -> Reminder | None:
        result = await self.session.execute(
            select(Reminder)
            .options(selectinload(Reminder.lead))
            .where(Reminder.id == reminder_id)
        )
        return result.scalar_one_or_none()

    async def get_group_id_for_lead(self, lead_id: int) -> int | None:
        result = await self.session.execute(
            select(Tenant.group_id)
            .join(Lead, Lead.tenant_id == Tenant.id)
            .where(Lead.id == lead_id)
        )
        return result.scalar_one_or_none()

    async def mark_reminder_sent(self, reminder_id: int) -> bool:
        result = await self.session.execute(
            update(Reminder)
            .where(Reminder.id == reminder_id)
            .values(
                is_sent=True,
                is_processing=False,
                processing_started_at=None,
            )
            .returning(Reminder.id)
        )
        return result.scalar_one_or_none() is not None

    async def cancel_reminder(self, reminder_id: int) -> bool:
        result = await self.session.execute(
            update(Reminder)
            .where(Reminder.id == reminder_id, Reminder.is_sent == False)
            .values(
                is_sent=True,
                is_processing=False,
                processing_started_at=None,
            )
            .returning(Reminder.id)
        )
        return result.scalar_one_or_none() is not None

    async def claim_reminder_for_delivery(
        self,
        reminder_id: int,
        *,
        stale_after_seconds: int = 300,
    ) -> bool:
        now = datetime.now(timezone.utc)
        stale_before = _naive(now) - timedelta(seconds=stale_after_seconds)
        result = await self.session.execute(
            update(Reminder)
            .where(
                Reminder.id == reminder_id,
                Reminder.is_sent == False,
                (
                    (Reminder.is_processing == False)
                    | (Reminder.processing_started_at.is_(None))
                    | (Reminder.processing_started_at <= stale_before)
                ),
            )
            .values(
                is_processing=True,
                processing_started_at=_naive(now),
                retry_count=Reminder.retry_count + 1,
            )
            .returning(Reminder.id)
        )
        return result.scalar_one_or_none() is not None

    async def release_reminder_after_failure(
        self,
        reminder_id: int,
        *,
        retry_at: datetime,
    ) -> bool:
        result = await self.session.execute(
            update(Reminder)
            .where(Reminder.id == reminder_id, Reminder.is_sent == False)
            .values(
                remind_at=_naive(retry_at),
                is_processing=False,
                processing_started_at=None,
            )
            .returning(Reminder.id)
        )
        return result.scalar_one_or_none() is not None

    # ── Менеджеры ─────────────────────────────────────

    async def get_manager_by_tg_id(
        self,
        tg_id: int,
        tenant_id: int | None = None,
    ) -> Manager | None:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="get_manager_by_tg_id",
        )
        query = select(Manager).where(
            Manager.tg_id == tg_id,
            Manager.is_active == True,
            Manager.tenant_id == scoped_tenant_id,
        )
        query = query.order_by(Manager.id.desc())
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_manager_by_tg_id_any(
        self,
        tg_id: int,
        tenant_id: int | None = None,
    ) -> Manager | None:
        query = select(Manager).where(Manager.tg_id == tg_id)
        if tenant_id is not None:
            query = query.where(Manager.tenant_id == tenant_id)
        query = query.order_by(Manager.id.desc())
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_all_managers(self, include_inactive: bool = False, tenant_id: int | None = None) -> list[Manager]:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="get_all_managers",
        )
        query = select(Manager)
        if not include_inactive:
            query = query.where(Manager.is_active == True)
        query = query.where(Manager.tenant_id == scoped_tenant_id)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_all_managers_any(self, include_inactive: bool = False) -> list[Manager]:
        query = select(Manager)
        if not include_inactive:
            query = query.where(Manager.is_active == True)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def count_active_managers(self, tenant_id: int | None = None) -> int:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="count_active_managers",
        )
        q = select(func.count(Manager.id)).where(
            Manager.is_active == True,
            Manager.tenant_id == scoped_tenant_id,
        )
        result = await self.session.execute(q)
        return result.scalar_one()

    async def count_active_managers_any(self) -> int:
        q = select(func.count(Manager.id)).where(Manager.is_active == True)
        result = await self.session.execute(q)
        return result.scalar_one()

    async def create_manager(
        self,
        tg_id: int,
        name: str,
        username: str | None,
        role: ManagerRole = ManagerRole.MANAGER,
        tenant_id: int | None = None,
    ) -> Manager:
        if isinstance(role, str):
            role_norm = role.strip().lower()
            role = ManagerRole(role_norm)
        manager = Manager(
            tg_id=tg_id,
            name=name,
            tg_username=username,
            role=role,
            tenant_id=tenant_id,
        )
        self.session.add(manager)
        await self.session.flush()
        return manager

    async def upsert_manager_from_contact(
        self,
        tg_id: int,
        name: str,
        username: str | None,
        role: ManagerRole = ManagerRole.MANAGER,
        tenant_id: int | None = None,
    ) -> Manager:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="upsert_manager_from_contact",
        )
        existing = await self.get_manager_by_tg_id_any(
            tg_id,
            tenant_id=scoped_tenant_id,
        )
        if existing:
            existing.name = name
            if username:
                existing.tg_username = username
            existing.is_active = True
            existing.tenant_id = scoped_tenant_id
            # Preserve admin role if already set
            if existing.role != ManagerRole.ADMIN:
                existing.role = role
            await self.session.flush()
            return existing

        return await self.create_manager(
            tg_id=tg_id,
            name=name,
            username=username,
            role=role,
            tenant_id=scoped_tenant_id,
        )

    async def set_manager_role(
        self,
        tg_id: int,
        role: ManagerRole,
        tenant_id: int | None = None,
    ) -> Manager | None:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="set_manager_role",
        )
        manager = await self.get_manager_by_tg_id(tg_id, tenant_id=scoped_tenant_id)
        if not manager:
            return None
        manager.role = role
        await self.session.flush()
        return manager

    async def deactivate_manager(self, tg_id: int, tenant_id: int | None = None) -> bool:
        scoped_tenant_id = self._require_tenant_scope(
            tenant_id,
            operation="deactivate_manager",
        )
        manager = await self.get_manager_by_tg_id(tg_id, tenant_id=scoped_tenant_id)
        if not manager:
            return False
        manager.is_active = False
        now = datetime.now(timezone.utc)
        reminders_query = [
            Reminder.manager_tg_id == tg_id,
            Reminder.is_sent == False,
            Reminder.remind_at > _naive(now),
        ]
        tenant_leads_subquery = select(Lead.id).where(Lead.tenant_id == scoped_tenant_id)
        reminders_query.append(Reminder.lead_id.in_(tenant_leads_subquery))
        await self.session.execute(
            update(Reminder)
            .where(*reminders_query)
            .values(
                is_sent=True,
                is_processing=False,
                processing_started_at=None,
            )
        )
        await self.session.flush()
        return True

    # ── Статистика ────────────────────────────────────

    async def count_by_status_period(
        self,
        status: LeadStatus,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        tenant_id: int | None = None,
    ) -> int:
        date_from, date_to = _naive(date_from), _naive(date_to)
        query = select(func.count()).select_from(Lead).where(Lead.status == status)
        if tenant_id is not None:
            query = query.where(Lead.tenant_id == tenant_id)
        if date_from:
            query = query.where(Lead.created_at >= date_from)
        if date_to:
            query = query.where(Lead.created_at <= date_to)
        result = await self.session.execute(query)
        return int(result.scalar() or 0)

    async def get_conversion_stats(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        tenant_id: int | None = None,
    ) -> dict:
        date_from, date_to = _naive(date_from), _naive(date_to)
        query = select(Lead.status, func.count()).select_from(Lead)
        if tenant_id is not None:
            query = query.where(Lead.tenant_id == tenant_id)
        if date_from:
            query = query.where(Lead.created_at >= date_from)
        if date_to:
            query = query.where(Lead.created_at <= date_to)
        query = query.group_by(Lead.status)

        result = await self.session.execute(query)
        rows = result.all()
        by_status = {s.value: 0 for s in LeadStatus}
        total = 0
        for status, count in rows:
            by_status[status.value] = count
            total += count

        return {
            "total": total,
            "by_status": by_status,
        }

    async def get_activity_stats(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        manager_id: int | None = None,
        tenant_id: int | None = None,
    ) -> dict:
        date_from, date_to = _naive(date_from), _naive(date_to)
        history_query = select(LeadHistory.lead_id).join(Lead, Lead.id == LeadHistory.lead_id)
        if tenant_id is not None:
            history_query = history_query.where(Lead.tenant_id == tenant_id)
        if date_from:
            history_query = history_query.where(LeadHistory.created_at >= date_from)
        if date_to:
            history_query = history_query.where(LeadHistory.created_at <= date_to)
        if manager_id:
            history_query = history_query.where(LeadHistory.manager_id == manager_id)

        created_query = select(Lead.id)
        if tenant_id is not None:
            created_query = created_query.where(Lead.tenant_id == tenant_id)
        if date_from:
            created_query = created_query.where(Lead.created_at >= date_from)
        if date_to:
            created_query = created_query.where(Lead.created_at <= date_to)
        if manager_id:
            created_query = created_query.where(Lead.manager_id == manager_id)

        lead_ids_subq = history_query.union(created_query).subquery()
        lead_ids_select = select(lead_ids_subq.c.lead_id)

        total_query = select(func.count()).select_from(Lead).where(Lead.id.in_(lead_ids_select))
        total_result = await self.session.execute(total_query)
        total = int(total_result.scalar() or 0)

        query = select(Lead.status, func.count()).select_from(Lead).where(Lead.id.in_(lead_ids_select))
        query = query.group_by(Lead.status)

        result = await self.session.execute(query)
        rows = result.all()
        by_status = {s.value: 0 for s in LeadStatus}
        for status, count in rows:
            by_status[status.value] = count

        return {
            "total": total,
            "by_status": by_status,
        }

