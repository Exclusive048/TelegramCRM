import asyncio
import os
import sys
import types
from types import SimpleNamespace

import pytest

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


class _FakeSession:
    def __init__(self, *, name: str, events: list[str], fail_commit: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_commit = fail_commit
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1
        self.events.append(f"{self.name}:commit")
        if self.fail_commit:
            raise RuntimeError(f"{self.name}_commit_failed")

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.events.append(f"{self.name}:rollback")


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _session_factory(sessions: list[_FakeSession]):
    queue = list(sessions)

    def _factory():
        if not queue:
            raise AssertionError("Unexpected AsyncSessionLocal() call")
        return _FakeSessionContext(queue.pop(0))

    return _factory


class _FakeLeadRepository:
    def __init__(self, session) -> None:
        self.session = session


def _tenant() -> SimpleNamespace:
    return SimpleNamespace(id=77, group_id=-100500, max_leads_per_month=-1)


def test_create_lead_side_effect_runs_after_main_commit(monkeypatch) -> None:
    events: list[str] = []
    main_session = _FakeSession(name="main", events=events)
    post_session = _FakeSession(name="post", events=events)

    class _FakeService:
        def __init__(self, repo, sender, group_id: int, tenant_id: int | None = None):
            self.repo = repo

        async def create_lead(self, payload):
            events.append("service:create")
            return SimpleNamespace(id=101, tg_message_id=None)

        async def sync_new_lead_card(self, lead_id: int):
            events.append("service:sync")
            assert main_session.commit_calls == 1
            return 555

    monkeypatch.setattr(leads, "AsyncSessionLocal", _session_factory([main_session, post_session]))
    monkeypatch.setattr(leads, "LeadRepository", _FakeLeadRepository)
    monkeypatch.setattr(leads, "LeadService", _FakeService)

    lead = asyncio.run(
        leads._create_lead_atomic(
            tenant=_tenant(),
            sender=object(),
            payload={"name": "A", "phone": "12345", "comment": "ok", "source": "api"},
            request_id="req-1",
            endpoint="/api/v1/leads",
            source="api",
        )
    )
    assert lead.id == 101
    assert lead.tg_message_id == 555
    assert events == ["service:create", "main:commit", "service:sync", "post:commit"]


def test_create_lead_commit_failure_does_not_run_side_effect(monkeypatch) -> None:
    events: list[str] = []
    main_session = _FakeSession(name="main", events=events, fail_commit=True)

    class _FakeService:
        def __init__(self, repo, sender, group_id: int, tenant_id: int | None = None):
            self.repo = repo

        async def create_lead(self, payload):
            events.append("service:create")
            return SimpleNamespace(id=102, tg_message_id=None)

        async def sync_new_lead_card(self, lead_id: int):
            events.append("service:sync")
            return 777

    monkeypatch.setattr(leads, "AsyncSessionLocal", _session_factory([main_session]))
    monkeypatch.setattr(leads, "LeadRepository", _FakeLeadRepository)
    monkeypatch.setattr(leads, "LeadService", _FakeService)

    with pytest.raises(RuntimeError, match="main_commit_failed"):
        asyncio.run(
            leads._create_lead_atomic(
                tenant=_tenant(),
                sender=object(),
                payload={"name": "A", "phone": "12345", "comment": "ok", "source": "api"},
                request_id="req-2",
                endpoint="/api/v1/leads",
                source="api",
            )
        )

    assert "service:sync" not in events
    assert main_session.rollback_calls == 1


def test_create_lead_post_commit_side_effect_failure_keeps_lead(monkeypatch) -> None:
    events: list[str] = []
    main_session = _FakeSession(name="main", events=events)
    post_session = _FakeSession(name="post", events=events)

    class _FakeService:
        def __init__(self, repo, sender, group_id: int, tenant_id: int | None = None):
            self.repo = repo

        async def create_lead(self, payload):
            events.append("service:create")
            return SimpleNamespace(id=103, tg_message_id=None)

        async def sync_new_lead_card(self, lead_id: int):
            events.append("service:sync")
            raise RuntimeError("telegram_send_failed")

    monkeypatch.setattr(leads, "AsyncSessionLocal", _session_factory([main_session, post_session]))
    monkeypatch.setattr(leads, "LeadRepository", _FakeLeadRepository)
    monkeypatch.setattr(leads, "LeadService", _FakeService)

    lead = asyncio.run(
        leads._create_lead_atomic(
            tenant=_tenant(),
            sender=object(),
            payload={"name": "A", "phone": "12345", "comment": "ok", "source": "api"},
            request_id="req-3",
            endpoint="/api/v1/leads",
            source="api",
        )
    )

    assert lead.id == 103
    assert lead.tg_message_id is None
    assert events == ["service:create", "main:commit", "service:sync", "post:rollback"]
