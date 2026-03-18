import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

from master_bot import handlers


class _FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.refresh_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def refresh(self, _obj) -> None:
        self.refresh_calls += 1


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Store:
    def __init__(self) -> None:
        self.tenants_by_owner: dict[int, list[SimpleNamespace]] = {}
        self.tenants_by_id: dict[int, SimpleNamespace] = {}
        self.referral_stats_by_tenant: dict[int, dict[str, int]] = {}
        self.ensure_management_key_calls: list[int] = []


class _FakeTenantRepo:
    def __init__(self, store: _Store) -> None:
        self.store = store

    async def get_tenants_by_owner(self, owner_tg_id: int):
        return list(self.store.tenants_by_owner.get(owner_tg_id, []))

    async def get_by_id(self, tenant_id: int):
        return self.store.tenants_by_id.get(tenant_id)

    async def ensure_management_api_key(self, tenant_id: int):
        self.store.ensure_management_key_calls.append(tenant_id)
        tenant = self.store.tenants_by_id.get(tenant_id)
        if tenant is not None:
            tenant.management_api_key = tenant.management_api_key or "mgmt-key-generated"
        return getattr(tenant, "management_api_key", "mgmt-key-generated")

    async def get_referral_stats(self, tenant_id: int):
        return self.store.referral_stats_by_tenant.get(
            tenant_id,
            {"total": 0, "paid": 0, "bonus_days_earned": 0},
        )


class _FakeMessage:
    def __init__(self, *, user_id: int = 7001, chat_type: str = "private") -> None:
        self.chat = SimpleNamespace(type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, username="owner", full_name="Owner")
        self.answers: list[dict] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, **kwargs})


class _FakeEditableMessage:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit_text(self, text: str, **kwargs):
        self.edits.append({"text": text, **kwargs})


class _FakeCallback:
    def __init__(self, *, data: str, user_id: int = 7001) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = _FakeEditableMessage()
        self.answer_calls: list[dict] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answer_calls.append({"text": text, "show_alert": show_alert})


def _tenant(
    tenant_id: int,
    *,
    owner_tg_id: int = 7001,
    company_name: str = "Acme",
    is_active: bool = True,
    api_key: str | None = "ingest-key",
    management_api_key: str | None = "management-key",
    referral_code: str = "REF123",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=tenant_id,
        owner_tg_id=owner_tg_id,
        company_name=company_name,
        is_active=is_active,
        api_key=api_key,
        management_api_key=management_api_key,
        referral_code=referral_code,
    )


def _patch_runtime(monkeypatch, store: _Store) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(handlers, "TenantRepository", lambda _session: _FakeTenantRepo(store))
    return session


def test_api_keys_command_opens_screen_for_single_tenant(monkeypatch) -> None:
    store = _Store()
    tenant = _tenant(1, management_api_key=None)
    store.tenants_by_owner[7001] = [tenant]
    store.tenants_by_id[1] = tenant
    _patch_runtime(monkeypatch, store)
    message = _FakeMessage()

    asyncio.run(handlers.cmd_api_keys(message))

    assert len(message.answers) == 1
    assert "API ключи" in message.answers[0]["text"]
    assert store.ensure_management_key_calls == [1]


def test_api_keys_command_shows_tenant_picker_for_multiple_accounts(monkeypatch) -> None:
    store = _Store()
    t1 = _tenant(1, company_name="Alpha")
    t2 = _tenant(2, company_name="Beta", is_active=False)
    store.tenants_by_owner[7001] = [t1, t2]
    store.tenants_by_id[1] = t1
    store.tenants_by_id[2] = t2
    _patch_runtime(monkeypatch, store)
    message = _FakeMessage()

    asyncio.run(handlers.cmd_api_keys(message))

    assert len(message.answers) == 1
    assert "Выберите аккаунт" in message.answers[0]["text"]
    markup = message.answers[0]["reply_markup"]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "acc:keys:1" in callbacks
    assert "acc:keys:2" in callbacks


def test_referral_command_opens_screen_for_single_tenant(monkeypatch) -> None:
    store = _Store()
    tenant = _tenant(10, referral_code="REF-ONE")
    store.tenants_by_owner[7001] = [tenant]
    store.tenants_by_id[10] = tenant
    store.referral_stats_by_tenant[10] = {"total": 3, "paid": 2, "bonus_days_earned": 28}
    _patch_runtime(monkeypatch, store)
    message = _FakeMessage()

    asyncio.run(handlers.cmd_referral(message))

    assert len(message.answers) == 1
    assert "Реферальная программа" in message.answers[0]["text"]
    assert "REF-ONE" in message.answers[0]["text"]


def test_referral_command_shows_tenant_picker_for_multiple_accounts(monkeypatch) -> None:
    store = _Store()
    t1 = _tenant(11, company_name="Gamma")
    t2 = _tenant(12, company_name="Delta")
    store.tenants_by_owner[7001] = [t1, t2]
    store.tenants_by_id[11] = t1
    store.tenants_by_id[12] = t2
    _patch_runtime(monkeypatch, store)
    message = _FakeMessage()

    asyncio.run(handlers.cmd_referral(message))

    assert len(message.answers) == 1
    assert "Выберите аккаунт" in message.answers[0]["text"]
    markup = message.answers[0]["reply_markup"]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "acc:ref:11" in callbacks
    assert "acc:ref:12" in callbacks


def test_no_tenants_command_returns_start_hint(monkeypatch) -> None:
    store = _Store()
    _patch_runtime(monkeypatch, store)
    message = _FakeMessage()

    asyncio.run(handlers.cmd_api_keys(message))

    assert len(message.answers) == 1
    assert "/start" in message.answers[0]["text"]


def test_existing_callback_flows_remain_operational(monkeypatch) -> None:
    store = _Store()
    tenant = _tenant(20, referral_code="REF-CB")
    store.tenants_by_id[20] = tenant
    store.referral_stats_by_tenant[20] = {"total": 1, "paid": 1, "bonus_days_earned": 14}
    _patch_runtime(monkeypatch, store)

    callback_keys = _FakeCallback(data="acc:keys:20")
    asyncio.run(handlers.cb_acc_keys(callback_keys))
    assert callback_keys.message.edits
    assert "API ключи" in callback_keys.message.edits[0]["text"]

    callback_ref = _FakeCallback(data="acc:ref:20")
    asyncio.run(handlers.cb_acc_ref(callback_ref))
    assert callback_ref.message.edits
    assert "Реферальная программа" in callback_ref.message.edits[0]["text"]


def test_master_entrypoint_registers_api_shortcuts() -> None:
    source = Path("app/entrypoints/master_bot.py").read_text(encoding="utf-8")
    assert 'BotCommand(command="api_keys"' in source
    assert 'BotCommand(command="referral"' in source
