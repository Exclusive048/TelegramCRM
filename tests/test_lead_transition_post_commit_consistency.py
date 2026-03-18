import asyncio
import os
import sys
import types
from types import SimpleNamespace

from app.db.models.lead import LeadStatus
from app.services.lead_service import LeadService

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

_fake_rate_limit = types.ModuleType("app.api.rate_limit")


class _FakeLimiter:
    def limit(self, *args, **kwargs):
        def _decorator(func):
            return func

        return _decorator


_fake_rate_limit.limiter = _FakeLimiter()
sys.modules["app.api.rate_limit"] = _fake_rate_limit

from app.api.routes import leads
from app.api.schemas.lead_schemas import LeadUpdateRequest, OkResponse
from starlette.requests import Request


class _FakeDb:
    def __init__(self, *, fail_first_commit: bool = False) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.fail_first_commit = fail_first_commit

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.fail_first_commit and self.commit_calls == 1:
            raise RuntimeError("commit_failed")

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _request(*, tenant_id: int = 77, group_id: int = -100500) -> Request:
    request = Request({"type": "http", "method": "PATCH", "path": "/api/v1/leads/1", "headers": []})
    request.state.tenant = SimpleNamespace(id=tenant_id, group_id=group_id)
    return request


def test_transition_side_effect_runs_after_commit(monkeypatch) -> None:
    class _FakeRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_id_scoped(self, lead_id: int, tenant_id: int | None = None):
            return {"id": lead_id}

    db = _FakeDb()
    events: list[str] = []

    class _FakeService:
        def __init__(self, repo, sender, group_id: int, tenant_id: int | None = None):
            self.repo = repo

        async def take_in_progress(self, lead_id, manager_tg_id, source_ref):
            events.append("transition")
            return SimpleNamespace(id=lead_id)

        async def mark_paid(self, lead_id, manager_tg_id, amount, source_ref):
            return None

        async def mark_success(self, lead_id, manager_tg_id, source_ref):
            return None

        async def reject_lead(self, lead_id, manager_tg_id, reason, source_ref):
            return None

        async def sync_lead_after_transition(self, lead_id: int, transition: str):
            events.append("sync")
            assert db.commit_calls == 1
            assert transition == "take"
            return None

    monkeypatch.setattr(leads, "LeadRepository", _FakeRepo)
    monkeypatch.setattr(leads, "LeadService", _FakeService)

    body = LeadUpdateRequest(status=LeadStatus.IN_PROGRESS, manager_tg_id=10)
    response = asyncio.run(
        leads.update_lead(
            lead_id=1,
            body=body,
            request=_request(),
            db=db,
            _=None,
            sender=object(),
        )
    )

    assert isinstance(response, OkResponse)
    assert events == ["transition", "sync"]
    assert db.commit_calls == 2
    assert db.rollback_calls == 0


def test_transition_commit_failure_blocks_side_effect(monkeypatch) -> None:
    class _FakeRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_id_scoped(self, lead_id: int, tenant_id: int | None = None):
            return {"id": lead_id}

    db = _FakeDb(fail_first_commit=True)
    sync_called = {"value": False}

    class _FakeService:
        def __init__(self, repo, sender, group_id: int, tenant_id: int | None = None):
            self.repo = repo

        async def take_in_progress(self, lead_id, manager_tg_id, source_ref):
            return SimpleNamespace(id=lead_id)

        async def mark_paid(self, lead_id, manager_tg_id, amount, source_ref):
            return None

        async def mark_success(self, lead_id, manager_tg_id, source_ref):
            return None

        async def reject_lead(self, lead_id, manager_tg_id, reason, source_ref):
            return None

        async def sync_lead_after_transition(self, lead_id: int, transition: str):
            sync_called["value"] = True
            return None

    monkeypatch.setattr(leads, "LeadRepository", _FakeRepo)
    monkeypatch.setattr(leads, "LeadService", _FakeService)

    body = LeadUpdateRequest(status=LeadStatus.IN_PROGRESS, manager_tg_id=10)
    try:
        asyncio.run(
            leads.update_lead(
                lead_id=1,
                body=body,
                request=_request(),
                db=db,
                _=None,
                sender=object(),
            )
        )
        raise AssertionError("expected commit failure")
    except RuntimeError as exc:
        assert "commit_failed" in str(exc)

    assert sync_called["value"] is False
    assert db.commit_calls == 1


def test_transition_post_commit_failure_does_not_rollback_lead(monkeypatch) -> None:
    class _FakeRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_id_scoped(self, lead_id: int, tenant_id: int | None = None):
            return {"id": lead_id}

    db = _FakeDb()

    class _FakeService:
        def __init__(self, repo, sender, group_id: int, tenant_id: int | None = None):
            self.repo = repo

        async def take_in_progress(self, lead_id, manager_tg_id, source_ref):
            return None

        async def mark_paid(self, lead_id, manager_tg_id, amount, source_ref):
            return SimpleNamespace(id=lead_id)

        async def mark_success(self, lead_id, manager_tg_id, source_ref):
            return None

        async def reject_lead(self, lead_id, manager_tg_id, reason, source_ref):
            return None

        async def sync_lead_after_transition(self, lead_id: int, transition: str):
            raise RuntimeError("telegram_failed")

    monkeypatch.setattr(leads, "LeadRepository", _FakeRepo)
    monkeypatch.setattr(leads, "LeadService", _FakeService)

    body = LeadUpdateRequest(status=LeadStatus.PAID, manager_tg_id=10, amount=500)
    response = asyncio.run(
        leads.update_lead(
            lead_id=1,
            body=body,
            request=_request(),
            db=db,
            _=None,
            sender=object(),
        )
    )

    assert isinstance(response, OkResponse)
    assert db.commit_calls == 1
    assert db.rollback_calls == 1


def test_transition_idempotency_no_duplicate_messages(monkeypatch) -> None:
    lead_obj = SimpleNamespace(
        id=1,
        status=LeadStatus.PAID,
        tg_message_id=10,
        tg_topic_id=100,
    )

    class _Repo:
        def __init__(self):
            self.session = object()
            self.active = SimpleNamespace(
                lead_id=1,
                chat_id=-100500,
                topic_id=100,
                message_id=10,
                is_active=True,
            )

        async def get_by_id_scoped(self, lead_id: int, tenant_id: int):
            return lead_obj

        async def get_active_card_message(self, lead_id: int):
            return self.active

        async def ensure_card_message(self, **kwargs):
            return None

        async def set_active_card_message(self, lead_id: int, chat_id: int, topic_id: int | None, message_id: int):
            self.active = SimpleNamespace(
                lead_id=lead_id,
                chat_id=chat_id,
                topic_id=topic_id,
                message_id=message_id,
                is_active=True,
            )
            lead_obj.tg_message_id = message_id
            lead_obj.tg_topic_id = topic_id
            return self.active

        async def set_tg_message(self, lead_id: int, message_id: int | None, topic_id: int | None):
            lead_obj.tg_message_id = message_id
            lead_obj.tg_topic_id = topic_id

        async def get_active_reminder(self, lead_id: int):
            return None

    class _Sender:
        def __init__(self):
            self.send_calls = 0

        async def send_message(self, **kwargs):
            self.send_calls += 1
            return SimpleNamespace(message_id=200 + self.send_calls)

    repo = _Repo()
    sender = _Sender()
    service = LeadService(repo=repo, sender=sender, group_id=-100500, tenant_id=77)

    async def _fake_resolve_topic_thread_id(*args, **kwargs):
        return 200

    monkeypatch.setattr("app.services.lead_service.resolve_topic_thread_id", _fake_resolve_topic_thread_id)

    async def _fake_archive_message(sender_obj, source_ref, text):
        return True

    async def _fake_build(self, lead):
        return "card", None

    monkeypatch.setattr("app.services.lead_service.archive_message", _fake_archive_message)
    monkeypatch.setattr("app.services.lead_service.format_archive_card", lambda lead: "archive")
    monkeypatch.setattr(LeadService, "_build_card_payload", _fake_build)

    asyncio.run(service.sync_lead_after_transition(1, "paid"))
    asyncio.run(service.sync_lead_after_transition(1, "paid"))

    assert sender.send_calls == 1
    assert repo.active.topic_id == 200
