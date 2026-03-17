import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

from master_bot import admin


class _FakeSession:
    async def commit(self) -> None:
        return


class _FakeSessionContext:
    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Store:
    def __init__(self) -> None:
        self.pending: dict[int, tuple[int, datetime]] = {}
        self.tenants: dict[int, object] = {}
        self.sent: list[tuple[int, str]] = []


class _FakeRepo:
    def __init__(self, store: _Store):
        self._store = store

    async def set_admin_pending_message(
        self,
        admin_tg_id: int,
        tenant_id: int,
        *,
        expires_at: datetime,
    ) -> None:
        self._store.pending[admin_tg_id] = (tenant_id, expires_at)

    async def get_admin_pending_tenant_id(self, admin_tg_id: int) -> int | None:
        row = self._store.pending.get(admin_tg_id)
        if not row:
            return None
        tenant_id, expires_at = row
        if expires_at < datetime.now(timezone.utc):
            self._store.pending.pop(admin_tg_id, None)
            return None
        return tenant_id

    async def clear_admin_pending_message(self, admin_tg_id: int) -> bool:
        return self._store.pending.pop(admin_tg_id, None) is not None

    async def get_by_id(self, tenant_id: int):
        return self._store.tenants.get(tenant_id)


class _FakeCallbackMessage:
    def __init__(self):
        self.edits: list[str] = []

    async def edit_text(self, text: str, reply_markup=None, parse_mode=None):
        self.edits.append(text)


class _FakeCallback:
    def __init__(self, user_id: int, data: str):
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.message = _FakeCallbackMessage()
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


class _FakePrivateMessage:
    def __init__(self, user_id: int, text: str):
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(type="private")
        self.text = text
        self.answers: list[tuple[str, str | None]] = []

    async def answer(self, text: str, parse_mode: str | None = None):
        self.answers.append((text, parse_mode))


def _patch_runtime(monkeypatch, store: _Store, *, admin_id: int) -> None:
    monkeypatch.setattr(admin.settings, "master_admin_tg_id", admin_id)
    monkeypatch.setattr(admin, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(admin, "TenantRepository", lambda session: _FakeRepo(store))

    async def _fake_notify(owner_tg_id: int, text: str):
        store.sent.append((owner_tg_id, text))

    monkeypatch.setattr(admin, "notify_tenant_owner", _fake_notify)


def test_admin_pending_state_not_process_local_dict_anymore() -> None:
    source = Path("master_bot/admin.py").read_text(encoding="utf-8")
    assert "_pending_msg" not in source
    assert "set_admin_pending_message" in source
    assert "get_admin_pending_tenant_id" in source
    assert "clear_admin_pending_message" in source


def test_pending_state_persists_for_send_flow_and_clears_on_success(monkeypatch) -> None:
    store = _Store()
    store.tenants[5] = SimpleNamespace(id=5, owner_tg_id=5005, company_name="Tenant 5")
    _patch_runtime(monkeypatch, store, admin_id=42)

    callback = _FakeCallback(user_id=42, data="adm:msg:5")
    asyncio.run(admin.cb_adm_msg(callback))
    assert 42 in store.pending

    message = _FakePrivateMessage(user_id=42, text="Hello from admin")
    asyncio.run(admin.handle_admin_freetext(message))

    assert store.sent == [(5005, "✉️ <b>Сообщение от администратора:</b>\n\nHello from admin")]
    assert 42 not in store.pending
    assert any("✅ Сообщение отправлено клиенту" in text for text, _ in message.answers)


def test_cancel_path_clears_pending_state(monkeypatch) -> None:
    store = _Store()
    store.pending[42] = (5, datetime.now(timezone.utc) + timedelta(hours=1))
    store.tenants[5] = SimpleNamespace(
        id=5,
        owner_tg_id=5005,
        company_name="Tenant 5",
        is_active=True,
        subscription_until=None,
        onboarding_completed=True,
        max_leads_per_month=-1,
        leads_this_month=0,
        api_key=None,
        plan="base",
    )
    _patch_runtime(monkeypatch, store, admin_id=42)

    callback = _FakeCallback(user_id=42, data="adm:detail:5")
    asyncio.run(admin.cb_adm_detail(callback))

    assert 42 not in store.pending
    assert callback.message.edits


def test_expired_pending_state_is_not_used_and_is_cleaned(monkeypatch) -> None:
    store = _Store()
    store.pending[42] = (5, datetime.now(timezone.utc) - timedelta(minutes=1))
    store.tenants[5] = SimpleNamespace(id=5, owner_tg_id=5005, company_name="Tenant 5")
    _patch_runtime(monkeypatch, store, admin_id=42)

    message = _FakePrivateMessage(user_id=42, text="Should not be sent")
    asyncio.run(admin.handle_admin_freetext(message))

    assert store.sent == []
    assert 42 not in store.pending
