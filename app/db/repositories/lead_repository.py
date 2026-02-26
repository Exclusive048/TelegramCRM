from datetime import datetime
from alembic.util import status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from app.db.models.lead import Lead, LeadStatus, LeadHistory, LeadComment, Manager, ManagerRole


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

        self.session.add(LeadHistory(
            lead_id=lead_id,
            from_status=old_status,
            to_status=new_status,
            manager_id=manager_id,
            comment=comment,
        ))
        await self.session.flush()
        return lead

    async def set_tg_message(self, lead_id: int, message_id: int, topic_id: int):
        await self.session.execute(
            update(Lead).where(Lead.id == lead_id)
            .values(tg_message_id=message_id, tg_topic_id=topic_id)
        )

    # ── Комментарии ───────────────────────────────────

    async def add_comment(self, lead_id: int, text: str, author: str) -> LeadComment:
        comment = LeadComment(lead_id=lead_id, text=text, author=author)
        self.session.add(comment)
        await self.session.flush()
        return comment

    # ── Менеджеры ─────────────────────────────────────

    async def get_manager_by_tg_id(self, tg_id: int) -> Manager | None:
        result = await self.session.execute(
            select(Manager).where(Manager.tg_id == tg_id, Manager.is_active == True)
        )
        return result.scalar_one_or_none()

    async def get_all_managers(self) -> list[Manager]:
        result = await self.session.execute(select(Manager).where(Manager.is_active == True))
        return result.scalars().all()

    async def create_manager(self, tg_id: int, name: str, username: str | None, role: ManagerRole = ManagerRole.MANAGER) -> Manager:
        if isinstance(role, str):
            role_norm = role.strip().lower()
            role = ManagerRole(role_norm)
        manager = Manager(tg_id=tg_id, name=name, tg_username=username, role=role)
        self.session.add(manager)
        await self.session.flush()
        return manager

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

    async def get_stats(self, date_from: datetime | None = None, date_to: datetime | None = None) -> dict:
        query = select(Lead)
        if date_from:
            query = query.where(Lead.created_at >= date_from)
        if date_to:
            query = query.where(Lead.created_at <= date_to)

        result = await self.session.execute(query)
        leads = result.scalars().all()

        total = len(leads)
        by_status = {s.value: 0 for s in LeadStatus}
        by_source = {}
        for lead in leads:
            by_status[lead.status.value] += 1
            by_source[lead.source] = by_source.get(lead.source, 0) + 1

        closed = by_status.get("closed", 0)
        not_rejected = total - by_status.get("rejected", 0)
        conversion = round(closed / not_rejected * 100) if not_rejected > 0 else 0

        return {
            "total": total,
            "by_status": by_status,
            "by_source": by_source,
            "conversion": conversion,
        }
