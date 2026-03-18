import asyncio
from types import SimpleNamespace

from aiogram.types import CallbackQuery, Message

from app.bot.middlewares import tenant_middleware


class _FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeTenantRepo:
    def __init__(self, tenants_by_chat: dict[int, object] | None = None) -> None:
        self.tenants_by_chat = tenants_by_chat or {}
        self.get_by_group_id_calls: list[int] = []

    async def get_by_group_id(self, chat_id: int):
        self.get_by_group_id_calls.append(chat_id)
        return self.tenants_by_chat.get(chat_id)

    async def deactivate(self, tenant_id: int) -> None:
        return None


def _callback(data: str, *, chat_id: int = -100500) -> CallbackQuery:
    raw = {
        "id": "cb-1",
        "from": {"id": 111, "is_bot": False, "first_name": "Owner"},
        "chat_instance": "instance",
        "data": data,
        "message": {
            "message_id": 101,
            "date": 0,
            "chat": {"id": chat_id, "type": "supergroup", "title": "crm"},
            "from": {"id": 777, "is_bot": True, "first_name": "bot"},
            "text": "choose",
        },
    }
    return CallbackQuery.model_validate(raw)


def _message(text: str, *, chat_id: int = -100500) -> Message:
    raw = {
        "message_id": 202,
        "date": 0,
        "chat": {"id": chat_id, "type": "supergroup", "title": "crm"},
        "from": {"id": 111, "is_bot": False, "first_name": "Owner"},
        "text": text,
    }
    return Message.model_validate(raw)


def test_setup_selection_callback_bypasses_tenant_not_found_and_reaches_handler(monkeypatch) -> None:
    middleware = tenant_middleware.TenantMiddleware()
    repo = _FakeTenantRepo()
    events: list[tuple[str, dict]] = []
    handler_calls: list[dict] = []

    async def _handler(event, data):
        handler_calls.append({"event": event, "data": dict(data)})
        return "handled"

    def _fake_emit(event_name: str, **payload):
        events.append((event_name, payload))

    monkeypatch.setattr(tenant_middleware, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_middleware, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(tenant_middleware, "emit_tg_event", _fake_emit)

    result = asyncio.run(
        middleware(
            _handler,
            _callback("setup:select:42"),
            {"sender": SimpleNamespace()},
        )
    )

    assert result == "handled"
    assert len(handler_calls) == 1
    assert repo.get_by_group_id_calls == [-100500]
    assert any(
        name == tenant_middleware.TG_MIDDLEWARE_EXIT
        and payload.get("skip_reason") == "setup_selection_callback_without_group_tenant"
        for name, payload in events
    )


def test_regular_callback_still_rejected_when_tenant_not_found(monkeypatch) -> None:
    middleware = tenant_middleware.TenantMiddleware()
    repo = _FakeTenantRepo()
    handler_called = False
    rejected_reasons: list[str] = []

    async def _handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "handled"

    def _fake_guard(reason: str, **kwargs):
        rejected_reasons.append(reason)

    monkeypatch.setattr(tenant_middleware, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_middleware, "TenantRepository", lambda session: repo)
    monkeypatch.setattr(tenant_middleware, "log_guard_rejected", _fake_guard)

    result = asyncio.run(
        middleware(
            _handler,
            _callback("panel:team"),
            {},
        )
    )

    assert result is None
    assert handler_called is False
    assert rejected_reasons == ["tenant_not_found"]


def test_setup_command_bypass_still_works_without_group_tenant(monkeypatch) -> None:
    middleware = tenant_middleware.TenantMiddleware()
    repo = _FakeTenantRepo()
    handler_called = False

    async def _handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "handled"

    monkeypatch.setattr(tenant_middleware, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(tenant_middleware, "TenantRepository", lambda session: repo)

    result = asyncio.run(
        middleware(
            _handler,
            _message("/setup"),
            {},
        )
    )

    assert result == "handled"
    assert handler_called is True
    assert repo.get_by_group_id_calls == []
