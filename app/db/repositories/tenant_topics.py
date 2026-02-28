from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.db.models.lead import TenantTopic


class TenantTopicRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_topic(
        self,
        chat_id: int,
        key: str,
        thread_id: int,
        title: str | None,
    ) -> None:
        result = await self.session.execute(
            select(TenantTopic).where(TenantTopic.chat_id == chat_id, TenantTopic.key == key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            await self.session.execute(
                update(TenantTopic)
                .where(TenantTopic.id == existing.id)
                .values(
                    thread_id=thread_id,
                    title=title,
                    updated_at=func.now(),
                )
            )
            return

        self.session.add(
            TenantTopic(
                chat_id=chat_id,
                key=key,
                thread_id=thread_id,
                title=title,
            )
        )

    async def get_topic_map(self, chat_id: int) -> dict[str, int]:
        result = await self.session.execute(
            select(TenantTopic.key, TenantTopic.thread_id).where(TenantTopic.chat_id == chat_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def get_thread_id(self, chat_id: int, key: str) -> int | None:
        result = await self.session.execute(
            select(TenantTopic.thread_id).where(
                TenantTopic.chat_id == chat_id,
                TenantTopic.key == key,
            )
        )
        return result.scalar_one_or_none()
