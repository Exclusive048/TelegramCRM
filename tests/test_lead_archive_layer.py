import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models.lead import Base, Lead, LeadArchive, LeadStatus
from app.db.repositories.lead_repository import LeadRepository

pytest.importorskip("aiosqlite")


def _run(coro):
    return asyncio.run(coro)


async def _new_session_factory() -> tuple[async_sessionmaker[AsyncSession], object]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    ), engine


async def _create_lead(repo: LeadRepository, *, tenant_id: int, name: str, source: str = "api") -> Lead:
    lead = await repo.create(
        {
            "tenant_id": tenant_id,
            "name": name,
            "phone": "+79001234567",
            "email": f"{name.lower()}@example.com",
            "source": source,
            "service": "audit",
            "comment": "initial",
            "utm_campaign": "camp",
            "utm_source": "google",
        }
    )
    lead.tg_topic_id = 101
    lead.tg_message_id = 202
    await repo.ensure_card_message(
        lead_id=lead.id,
        chat_id=-100123,
        topic_id=101,
        message_id=202,
        is_active=True,
    )
    return lead


async def _finalize_success(repo: LeadRepository, lead_id: int) -> None:
    assert await repo.try_take_lead(lead_id, manager_id=None)
    assert await repo.mark_paid(lead_id, manager_id=None, amount=500.0)
    assert await repo.mark_success(lead_id, manager_id=None)


async def _finalize_rejected(repo: LeadRepository, lead_id: int, *, reason: str) -> None:
    assert await repo.reject_lead(lead_id, manager_id=None, reason=reason)


def test_archive_record_created_with_history_and_context() -> None:
    async def _scenario() -> None:
        session_factory, engine = await _new_session_factory()
        try:
            async with session_factory() as session:
                repo = LeadRepository(session)
                lead = await _create_lead(repo, tenant_id=11, name="LeadA")
                await _finalize_success(repo, lead.id)
                await session.commit()

                archive = (
                    await session.execute(
                        select(LeadArchive).where(LeadArchive.source_lead_id == lead.id)
                    )
                ).scalar_one()

                assert archive.tenant_id == 11
                assert archive.final_status == LeadStatus.SUCCESS
                assert archive.tg_chat_id == -100123
                assert archive.tg_topic_id == 101
                assert archive.tg_message_id == 202
                assert archive.lead_created_at is not None
                assert archive.lead_closed_at is not None
                assert archive.archived_at is not None

                history = archive.status_history
                assert isinstance(history, list)
                assert history[0]["to_status"] == LeadStatus.NEW.value
                assert history[-1]["to_status"] == LeadStatus.SUCCESS.value
                assert all(event.get("created_at") for event in history)
        finally:
            await engine.dispose()

    _run(_scenario())


def test_archive_idempotency_no_duplicates_for_same_lead() -> None:
    async def _scenario() -> None:
        session_factory, engine = await _new_session_factory()
        try:
            async with session_factory() as session:
                repo = LeadRepository(session)
                lead = await _create_lead(repo, tenant_id=21, name="LeadB")
                await _finalize_rejected(repo, lead.id, reason="not_target")
                await session.commit()

                assert await repo.archive_lead_snapshot_if_final(lead.id)
                assert await repo.archive_lead_snapshot_if_final(lead.id)
                assert await repo.archive_lead_snapshot_if_final_scoped(
                    lead.id,
                    tenant_id=21,
                )
                await session.commit()

                count = (
                    await session.execute(
                        select(func.count()).select_from(LeadArchive).where(
                            LeadArchive.source_lead_id == lead.id
                        )
                    )
                ).scalar_one()
                assert count == 1
        finally:
            await engine.dispose()

    _run(_scenario())


def test_live_logic_stays_intact_after_archive_layer() -> None:
    async def _scenario() -> None:
        session_factory, engine = await _new_session_factory()
        try:
            async with session_factory() as session:
                repo = LeadRepository(session)
                lead = await _create_lead(repo, tenant_id=31, name="LeadC")
                await _finalize_success(repo, lead.id)
                await session.commit()

                live = await repo.get_by_id_scoped(lead.id, tenant_id=31)
                assert live is not None
                assert live.status == LeadStatus.SUCCESS
                assert live.closed_at is not None

                success_count = await repo.count_by_status_period(
                    LeadStatus.SUCCESS,
                    tenant_id=31,
                )
                assert success_count == 1

                conversion = await repo.get_conversion_stats(tenant_id=31)
                assert conversion["by_status"][LeadStatus.SUCCESS.value] == 1
        finally:
            await engine.dispose()

    _run(_scenario())


def test_archive_report_analytics_and_tenant_isolation() -> None:
    async def _scenario() -> None:
        session_factory, engine = await _new_session_factory()
        try:
            async with session_factory() as session:
                repo = LeadRepository(session)

                lead_success_recent = await _create_lead(repo, tenant_id=41, name="LeadD")
                await _finalize_success(repo, lead_success_recent.id)

                lead_reject_recent = await _create_lead(repo, tenant_id=41, name="LeadE")
                await _finalize_rejected(repo, lead_reject_recent.id, reason="no_budget")

                lead_success_old = await _create_lead(repo, tenant_id=41, name="LeadF")
                await _finalize_success(repo, lead_success_old.id)

                lead_other_tenant = await _create_lead(repo, tenant_id=42, name="LeadG")
                await _finalize_success(repo, lead_other_tenant.id)

                old_live = await repo.get_by_id_scoped(lead_success_old.id, tenant_id=41)
                assert old_live is not None
                old_live.closed_at = (datetime.now(timezone.utc) - timedelta(days=45)).replace(
                    tzinfo=None
                )
                await session.flush()
                assert await repo.archive_lead_snapshot_if_final_scoped(
                    lead_success_old.id,
                    tenant_id=41,
                )
                await session.commit()

                date_from = datetime.now(timezone.utc) - timedelta(days=7)
                date_to = datetime.now(timezone.utc) + timedelta(days=1)

                report_tenant_41, total_41 = await repo.get_archive_report_scoped(
                    date_from=date_from,
                    date_to=date_to,
                    tenant_id=41,
                    per_page=100,
                )
                ids_41 = {item.source_lead_id for item in report_tenant_41}
                assert total_41 == 2
                assert ids_41 == {lead_success_recent.id, lead_reject_recent.id}
                assert all(item.tenant_id == 41 for item in report_tenant_41)

                analytics_41 = await repo.get_archive_status_analytics_scoped(
                    date_from=date_from,
                    date_to=date_to,
                    tenant_id=41,
                )
                assert analytics_41[LeadStatus.SUCCESS.value] == 1
                assert analytics_41[LeadStatus.REJECTED.value] == 1

                report_tenant_42, total_42 = await repo.get_archive_report_scoped(
                    date_from=date_from,
                    date_to=date_to,
                    tenant_id=42,
                    per_page=100,
                )
                assert total_42 == 1
                assert report_tenant_42[0].source_lead_id == lead_other_tenant.id
                assert report_tenant_42[0].tenant_id == 42
        finally:
            await engine.dispose()

    _run(_scenario())
