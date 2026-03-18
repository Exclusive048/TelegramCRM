import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

from app.bot.handlers import setup
from app.db.repositories import tenant_repository as tenant_repository_module


def _tenant(tenant_id: int, *, owner_tg_id: int, group_id: int, company_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=tenant_id,
        owner_tg_id=owner_tg_id,
        group_id=group_id,
        company_name=company_name,
    )


class _FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeTenantRepo:
    def __init__(
        self,
        *,
        tenants_by_owner: dict[int, list[SimpleNamespace]] | None = None,
        tenant_by_id: dict[int, SimpleNamespace] | None = None,
    ) -> None:
        self.tenants_by_owner = tenants_by_owner or {}
        self.tenant_by_id = tenant_by_id or {}
        self.get_by_id_calls = 0

    async def get_by_owner(self, owner_tg_id: int):
        return list(self.tenants_by_owner.get(owner_tg_id, []))

    async def get_by_id(self, tenant_id: int):
        self.get_by_id_calls += 1
        return self.tenant_by_id.get(tenant_id)


class _FakeSender:
    def __init__(self) -> None:
        self.ephemeral_calls: list[dict] = []

    async def send_ephemeral_text(
        self,
        *,
        chat_id: int,
        text: str,
        ttl_sec: int,
        message_thread_id: int | None = None,
        **kwargs,
    ):
        self.ephemeral_calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "ttl_sec": ttl_sec,
                "message_thread_id": message_thread_id,
                **kwargs,
            }
        )
        return SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_id=900 + len(self.ephemeral_calls),
            message_thread_id=message_thread_id,
        )


class _FakeCallback:
    def __init__(
        self,
        *,
        user_id: int,
        chat_id: int,
        thread_id: int,
        message_id: int,
        tenant_id: int,
    ) -> None:
        self.data = f"setup:select:{tenant_id}"
        self.from_user = SimpleNamespace(id=user_id, full_name="Owner", username="owner")
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_thread_id=thread_id,
            message_id=message_id,
        )
        self.answers: list[dict] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append({"text": text, "show_alert": show_alert})


def _message(
    *,
    user_id: int = 111,
    chat_id: int = -100500,
    thread_id: int = 77,
    message_id: int = 501,
) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id, full_name="Owner", username="owner"),
        message_thread_id=thread_id,
        message_id=message_id,
    )


def test_setup_with_ambiguous_unbound_shows_explicit_selection(monkeypatch) -> None:
    repo = _FakeTenantRepo(
        tenants_by_owner={
            111: [
                _tenant(1, owner_tg_id=111, group_id=0, company_name="Alpha"),
                _tenant(2, owner_tg_id=111, group_id=0, company_name="Beta"),
            ]
        }
    )
    sender = _FakeSender()
    run_calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    async def _fake_prereqs(*args, **kwargs):
        return True

    async def _fake_run_setup(**kwargs):
        run_calls.append(kwargs)

    def _fake_emit(event_name: str, **payload):
        events.append((event_name, payload))

    monkeypatch.setattr(setup, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_repository_module, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(setup, "_ensure_setup_prerequisites", _fake_prereqs)
    monkeypatch.setattr(setup, "_run_setup_for_tenant", _fake_run_setup)
    monkeypatch.setattr(setup, "emit_tg_event", _fake_emit)

    asyncio.run(setup.cmd_setup(_message(), sender))

    assert run_calls == []
    assert sender.ephemeral_calls
    call = sender.ephemeral_calls[0]
    assert "Выберите, какой аккаунт привязать" in call["text"]
    markup = call.get("reply_markup")
    assert markup is not None
    callback_data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callback_data == ["setup:select:1", "setup:select:2"]
    assert any(name == "tg_setup_selection_shown" for name, _ in events)


def test_setup_callback_allows_owner_and_routes_into_shared_setup(monkeypatch) -> None:
    tenant = _tenant(10, owner_tg_id=111, group_id=0, company_name="Owner Tenant")
    repo = _FakeTenantRepo(tenant_by_id={10: tenant})
    sender = _FakeSender()
    callback = _FakeCallback(user_id=111, chat_id=-100500, thread_id=77, message_id=601, tenant_id=10)
    run_calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    async def _fake_prereqs(*args, **kwargs):
        return True

    async def _fake_run_setup(**kwargs):
        run_calls.append(kwargs)

    def _fake_emit(event_name: str, **payload):
        events.append((event_name, payload))

    monkeypatch.setattr(setup, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_repository_module, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(setup, "_ensure_setup_prerequisites", _fake_prereqs)
    monkeypatch.setattr(setup, "_run_setup_for_tenant", _fake_run_setup)
    monkeypatch.setattr(setup, "emit_tg_event", _fake_emit)

    asyncio.run(setup.cb_setup_select_tenant(callback, sender))

    assert len(run_calls) == 1
    assert run_calls[0]["target_tenant"] is tenant
    assert run_calls[0]["should_bind_tenant"] is True
    assert any(call["show_alert"] is False for call in callback.answers)
    assert any(name == "tg_setup_tenant_selected" for name, _ in events)


def test_setup_callback_rejects_tenant_of_another_owner(monkeypatch) -> None:
    tenant = _tenant(15, owner_tg_id=222, group_id=0, company_name="Foreign")
    repo = _FakeTenantRepo(tenant_by_id={15: tenant})
    sender = _FakeSender()
    callback = _FakeCallback(user_id=111, chat_id=-100500, thread_id=77, message_id=602, tenant_id=15)
    run_calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    async def _fake_prereqs(*args, **kwargs):
        return True

    async def _fake_run_setup(**kwargs):
        run_calls.append(kwargs)

    def _fake_emit(event_name: str, **payload):
        events.append((event_name, payload))

    monkeypatch.setattr(setup, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_repository_module, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(setup, "_ensure_setup_prerequisites", _fake_prereqs)
    monkeypatch.setattr(setup, "_run_setup_for_tenant", _fake_run_setup)
    monkeypatch.setattr(setup, "emit_tg_event", _fake_emit)

    asyncio.run(setup.cb_setup_select_tenant(callback, sender))

    assert run_calls == []
    assert any(call["show_alert"] is True for call in callback.answers)
    assert any(
        name == "tg_setup_selection_rejected" and payload.get("reason") == "tenant_owner_mismatch"
        for name, payload in events
    )


def test_setup_callback_rejects_stale_non_unbound_tenant(monkeypatch) -> None:
    tenant = _tenant(19, owner_tg_id=111, group_id=-100777, company_name="Moved")
    repo = _FakeTenantRepo(tenant_by_id={19: tenant})
    sender = _FakeSender()
    callback = _FakeCallback(user_id=111, chat_id=-100500, thread_id=77, message_id=604, tenant_id=19)
    run_calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    async def _fake_prereqs(*args, **kwargs):
        return True

    async def _fake_run_setup(**kwargs):
        run_calls.append(kwargs)

    def _fake_emit(event_name: str, **payload):
        events.append((event_name, payload))

    monkeypatch.setattr(setup, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_repository_module, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(setup, "_ensure_setup_prerequisites", _fake_prereqs)
    monkeypatch.setattr(setup, "_run_setup_for_tenant", _fake_run_setup)
    monkeypatch.setattr(setup, "emit_tg_event", _fake_emit)

    asyncio.run(setup.cb_setup_select_tenant(callback, sender))

    assert run_calls == []
    assert any(call["show_alert"] is True for call in callback.answers)
    assert any(
        name == "tg_setup_selection_rejected" and payload.get("reason") == "tenant_not_unbound"
        for name, payload in events
    )


def test_setup_callback_does_not_continue_when_prerequisites_fail(monkeypatch) -> None:
    repo = _FakeTenantRepo(
        tenant_by_id={21: _tenant(21, owner_tg_id=111, group_id=0, company_name="Owner Tenant")}
    )
    sender = _FakeSender()
    callback = _FakeCallback(user_id=111, chat_id=-100500, thread_id=77, message_id=603, tenant_id=21)
    run_calls: list[dict] = []

    async def _fake_prereqs(*args, **kwargs):
        return False

    async def _fake_run_setup(**kwargs):
        run_calls.append(kwargs)

    monkeypatch.setattr(setup, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_repository_module, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(setup, "_ensure_setup_prerequisites", _fake_prereqs)
    monkeypatch.setattr(setup, "_run_setup_for_tenant", _fake_run_setup)

    asyncio.run(setup.cb_setup_select_tenant(callback, sender))

    assert repo.get_by_id_calls == 0
    assert run_calls == []
    assert any(call["show_alert"] is True for call in callback.answers)


def test_setup_command_single_unbound_still_uses_shared_setup_path(monkeypatch) -> None:
    repo = _FakeTenantRepo(
        tenants_by_owner={111: [_tenant(31, owner_tg_id=111, group_id=0, company_name="Solo")]}
    )
    sender = _FakeSender()
    run_calls: list[dict] = []

    async def _fake_prereqs(*args, **kwargs):
        return True

    async def _fake_run_setup(**kwargs):
        run_calls.append(kwargs)

    monkeypatch.setattr(setup, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_repository_module, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(setup, "_ensure_setup_prerequisites", _fake_prereqs)
    monkeypatch.setattr(setup, "_run_setup_for_tenant", _fake_run_setup)

    asyncio.run(setup.cmd_setup(_message(), sender))

    assert len(run_calls) == 1
    assert run_calls[0]["target_tenant"].id == 31
    assert run_calls[0]["should_bind_tenant"] is True


def test_setup_command_bound_tenant_still_uses_shared_setup_without_bind(monkeypatch) -> None:
    repo = _FakeTenantRepo(
        tenants_by_owner={111: [_tenant(41, owner_tg_id=111, group_id=-100500, company_name="Bound")]}
    )
    sender = _FakeSender()
    run_calls: list[dict] = []

    async def _fake_prereqs(*args, **kwargs):
        return True

    async def _fake_run_setup(**kwargs):
        run_calls.append(kwargs)

    monkeypatch.setattr(setup, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_repository_module, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(setup, "_ensure_setup_prerequisites", _fake_prereqs)
    monkeypatch.setattr(setup, "_run_setup_for_tenant", _fake_run_setup)

    asyncio.run(setup.cmd_setup(_message(chat_id=-100500), sender))

    assert len(run_calls) == 1
    assert run_calls[0]["target_tenant"].id == 41
    assert run_calls[0]["should_bind_tenant"] is False
