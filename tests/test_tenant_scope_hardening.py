import asyncio
from types import SimpleNamespace

import pytest
from aiogram.enums import ChatMemberStatus

from app.core import permissions
from app.db.repositories.lead_repository import LeadRepository
from app.services.lead_service import LeadService


def test_repository_tenant_scoped_methods_reject_none_tenant() -> None:
    repo = LeadRepository(session=object())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="get_manager_by_tg_id"):
        asyncio.run(repo.get_manager_by_tg_id(1001, tenant_id=None))

    with pytest.raises(ValueError, match="get_all_managers"):
        asyncio.run(repo.get_all_managers(tenant_id=None))

    with pytest.raises(ValueError, match="count_active_managers"):
        asyncio.run(repo.count_active_managers(tenant_id=None))

    with pytest.raises(ValueError, match="upsert_manager_from_contact"):
        asyncio.run(
            repo.upsert_manager_from_contact(
                tg_id=1001,
                name="Manager",
                username="mgr",
                tenant_id=None,
            )
        )

    with pytest.raises(ValueError, match="get_by_id_scoped"):
        asyncio.run(repo.get_by_id_scoped(lead_id=1, tenant_id=None))

    with pytest.raises(ValueError, match="get_list_scoped"):
        asyncio.run(repo.get_list_scoped(tenant_id=None))

    with pytest.raises(ValueError, match="get_archive_report_scoped"):
        asyncio.run(repo.get_archive_report_scoped(tenant_id=None))

    with pytest.raises(ValueError, match="get_archive_status_analytics_scoped"):
        asyncio.run(repo.get_archive_status_analytics_scoped(tenant_id=None))

    with pytest.raises(ValueError, match="archive_lead_snapshot_if_final_scoped"):
        asyncio.run(repo.archive_lead_snapshot_if_final_scoped(lead_id=1, tenant_id=None))


def test_is_any_manager_uses_explicit_global_lookup() -> None:
    class _Repo:
        async def get_manager_by_tg_id_any(self, tg_id: int):
            return SimpleNamespace(is_active=True)

    assert asyncio.run(permissions.is_any_manager(_Repo(), 777)) is True


def test_is_crm_admin_fails_closed_without_tenant_scope() -> None:
    class _Sender:
        async def get_chat_member(self, chat_id: int, user_id: int):
            return SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)

    class _Repo:
        async def get_manager_by_tg_id(self, tg_id: int, tenant_id: int):
            raise AssertionError("tenant-scoped manager lookup must not run when tenant_id is missing")

    allowed = asyncio.run(
        permissions.is_crm_admin(
            sender=_Sender(),
            repo=_Repo(),
            chat_id=-100500,
            tg_id=777,
            tenant_id=None,
        )
    )
    assert allowed is False


def test_lead_service_blocks_transition_without_tenant_scope() -> None:
    class _Repo:
        def __init__(self) -> None:
            self.try_take_called = False

        async def get_by_id_scoped(self, lead_id: int, tenant_id: int):
            raise AssertionError("lead scope lookup should not run when tenant scope is missing")

        async def try_take_lead(self, lead_id: int, manager_id: int | None):
            self.try_take_called = True
            return SimpleNamespace(id=lead_id)

    service = LeadService(
        repo=_Repo(),  # type: ignore[arg-type]
        sender=object(),  # type: ignore[arg-type]
        group_id=-100500,
        tenant_id=None,
    )

    result = asyncio.run(service.take_in_progress(lead_id=1, manager_tg_id=777, source_ref=None))
    assert result is None
    assert service.repo.try_take_called is False
