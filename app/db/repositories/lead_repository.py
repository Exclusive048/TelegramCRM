from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from app.db.models.lead import (
    Lead,
    LeadStatus,
    LeadHistory,
    LeadComment,
    Manager,
    ManagerRole,
    PanelMessage,
    LeadCardMessage,
    Reminder,
)


class LeadRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

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

    async def get_by_id(self, lead_id: int) -> Lead | None:
        result = await self.session.execute(
            select(Lead)
            .options(selectinload(Lead.manager), selectinload(Lead.history), selectinload(Lead.comments))
            .where(Lead.id == lead_id)
        )
        return result.scalar_one_or_none()

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
    ) -> tuple[list[Lead], int]:
        query = select(Lead).options(selectinload(Lead.manager))
        if status:
            query = query.where(Lead.status == status)
        if source:
            query = query.where(Lead.source == source)
        if manager_id:
            query = query.where(Lead.manager_id == manager_id)
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

    # ── Обновление ────────────────────────────────────

    async def update_status(
        self,
        lead_id: int,
        new_status: LeadStatus,
        manager_id: int | None = None,
        comment: str | None = None,
        reject_reason: str | None = None,
    ) -> Lead | None:
        lead = await self.get_by_id(lead_id)
        if not lead:
            return None
        old_status = lead.status
        lead.status = new_status
        if manager_id:
            lead.manager_id = manager_id
        if reject_reason:
            lead.reject_reason = reject_reason
        if new_status in (LeadStatus.SUCCESS, LeadStatus.REJECTED):
            lead.closed_at = datetime.now(timezone.utc)

        self.session.add(LeadHistory(
            lead_id=lead_id,
            from_status=old_status,
            to_status=new_status,
            manager_id=manager_id,
            comment=comment,
        ))
        await self.session.flush()
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
        return await self.get_by_id(lead_id)

    async def set_tg_message(self, lead_id: int, message_id: int | None, topic_id: int | None):
        await self.session.execute(
            update(Lead).where(Lead.id == lead_id)
            .values(tg_message_id=message_id, tg_topic_id=topic_id, updated_at=func.now())
        )

    # ── Lead card messages ───────────────────────────────────────────────────

    async def get_card_message(self, chat_id: int, message_id: int) -> LeadCardMessage | None:
        result = await self.session.execute(
            select(LeadCardMessage).where(
                LeadCardMessage.chat_id == chat_id,
                LeadCardMessage.message_id == message_id,
            )
        )
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

    async def add_comment(self, lead_id: int, text: str, author: str) -> LeadComment:
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
            remind_at=remind_at,
            message=message,
            is_sent=False,
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def get_pending_reminders(self) -> list[Reminder]:
        result = await self.session.execute(
            select(Reminder)
            .where(Reminder.is_sent == False)
            .order_by(Reminder.remind_at.asc())
        )
        return result.scalars().all()

    async def get_reminder_by_id(self, reminder_id: int) -> Reminder | None:
        result = await self.session.execute(
            select(Reminder)
            .options(selectinload(Reminder.lead))
            .where(Reminder.id == reminder_id)
        )
        return result.scalar_one_or_none()

    async def mark_reminder_sent(self, reminder_id: int) -> bool:
        result = await self.session.execute(
            update(Reminder)
            .where(Reminder.id == reminder_id)
            .values(is_sent=True)
            .returning(Reminder.id)
        )
        return result.scalar_one_or_none() is not None

    # ── Менеджеры ─────────────────────────────────────

    async def get_manager_by_tg_id(self, tg_id: int) -> Manager | None:
        result = await self.session.execute(
            select(Manager).where(Manager.tg_id == tg_id, Manager.is_active == True)
        )
        return result.scalar_one_or_none()

    async def get_manager_by_tg_id_any(self, tg_id: int) -> Manager | None:
        result = await self.session.execute(
            select(Manager).where(Manager.tg_id == tg_id)
        )
        return result.scalar_one_or_none()

    async def get_all_managers(self, include_inactive: bool = False) -> list[Manager]:
        query = select(Manager)
        if not include_inactive:
            query = query.where(Manager.is_active == True)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def create_manager(self, tg_id: int, name: str, username: str | None, role: ManagerRole = ManagerRole.MANAGER) -> Manager:
        if isinstance(role, str):
            role_norm = role.strip().lower()
            role = ManagerRole(role_norm)
        manager = Manager(tg_id=tg_id, name=name, tg_username=username, role=role)
        self.session.add(manager)
        await self.session.flush()
        return manager

    async def upsert_manager_from_contact(
        self,
        tg_id: int,
        name: str,
        username: str | None,
        role: ManagerRole = ManagerRole.MANAGER,
    ) -> Manager:
        existing = await self.get_manager_by_tg_id_any(tg_id)
        if existing:
            existing.name = name
            if username:
                existing.tg_username = username
            existing.is_active = True
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
        )

    async def set_manager_role(self, tg_id: int, role: ManagerRole) -> Manager | None:
        manager = await self.get_manager_by_tg_id(tg_id)
        if not manager:
            return None
        manager.role = role
        await self.session.flush()
        return manager

    async def deactivate_manager(self, tg_id: int) -> bool:
        manager = await self.get_manager_by_tg_id(tg_id)
        if not manager:
            return False
        manager.is_active = False
        await self.session.flush()
        return True

    # ── Статистика ────────────────────────────────────

    async def count_by_status_period(
        self,
        status: LeadStatus,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        query = select(func.count()).select_from(Lead).where(Lead.status == status)
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
    ) -> dict:
        query = select(Lead.status, func.count()).select_from(Lead)
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
    ) -> dict:
        history_query = select(LeadHistory.lead_id)
        if date_from:
            history_query = history_query.where(LeadHistory.created_at >= date_from)
        if date_to:
            history_query = history_query.where(LeadHistory.created_at <= date_to)
        if manager_id:
            history_query = history_query.where(LeadHistory.manager_id == manager_id)

        created_query = select(Lead.id)
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

