import asyncio
import os
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

from app.db.repositories.tenant_repository import TenantRepository
from master_bot import handlers


class _FakeCreateSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_calls = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1
        for obj in self.added:
            management_key = getattr(obj, "management_api_key", None)
            if management_key in (None, ""):
                raise AssertionError("management_api_key must be set before flush")


class _CreateRepo(TenantRepository):
    async def get_by_referral_code(self, code: str):
        return None

    async def get_by_api_key_any(self, api_key: str):
        return None


def test_create_tenant_sets_management_and_ingest_keys_before_flush() -> None:
    session = _FakeCreateSession()
    repo = _CreateRepo(session)

    tenant = asyncio.run(
        repo.create_tenant(
            owner_tg_id=1001,
            company_name="Acme",
            referred_by_id=None,
        )
    )

    assert session.flush_calls == 1
    assert tenant.management_api_key
    assert tenant.api_key
    assert tenant.management_api_key != tenant.api_key


def test_create_without_ingest_key_still_sets_management_key() -> None:
    session = _FakeCreateSession()
    repo = _CreateRepo(session)

    tenant = asyncio.run(
        repo.create(
            owner_tg_id=1002,
            company_name="NoIngest",
            referred_by_id=None,
            generate_api_key=False,
        )
    )

    assert tenant.management_api_key
    assert tenant.api_key is None


class _FailingTenantRepo:
    async def create_tenant(self, **kwargs):
        raise IntegrityError("INSERT tenants", params={}, orig=Exception("not null"))

    async def get_by_referral_code(self, code: str):
        return None


class _FakeLeadRepo:
    async def upsert_manager_from_contact(self, **kwargs):
        return None


class _FakeSession:
    async def commit(self) -> None:
        return None


class _FakeSessionContext:
    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeState:
    def __init__(self) -> None:
        self.cleared = False

    async def get_data(self) -> dict:
        return {}

    async def clear(self) -> None:
        self.cleared = True


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=7001, full_name="Owner", username="owner")
        self.answers: list[tuple[str, str | None]] = []

    async def answer(self, text: str, parse_mode: str | None = None):
        self.answers.append((text, parse_mode))


def test_master_onboarding_create_failure_is_user_visible_and_structured(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, **payload):
        events.append((event_name, payload))

    async def _fake_notify_admin(_text: str):
        return None

    monkeypatch.setattr(handlers, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(handlers, "TenantRepository", lambda session: _FailingTenantRepo())
    monkeypatch.setattr(handlers, "LeadRepository", lambda session: _FakeLeadRepo())
    monkeypatch.setattr(handlers, "emit_tg_event", _fake_emit)
    monkeypatch.setattr(handlers, "notify_admin", _fake_notify_admin)

    message = _FakeMessage("Acme")
    state = _FakeState()

    asyncio.run(handlers.handle_company_name(message, state))

    assert state.cleared is True
    assert any("Не удалось создать CRM-аккаунт" in text for text, _ in message.answers)
    assert any(
        name == "tg_handler_failed" and payload.get("step") == "create_tenant"
        for name, payload in events
    )
