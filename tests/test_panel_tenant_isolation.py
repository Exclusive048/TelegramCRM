import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

from app.bot.handlers import panel


class _FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1


class _FakeSessionContext:
    def __init__(self, session: _FakeSession):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRepo:
    def __init__(self) -> None:
        self.get_all_calls: list[dict] = []
        self.upsert_calls: list[dict] = []
        self.set_panel_calls: list[tuple[int, int, int]] = []

    async def get_all_managers(self, include_inactive: bool = False, tenant_id: int | None = None):
        self.get_all_calls.append(
            {
                "include_inactive": include_inactive,
                "tenant_id": tenant_id,
            }
        )
        return []

    async def upsert_manager_from_contact(
        self,
        tg_id: int,
        name: str,
        username: str | None,
        tenant_id: int | None = None,
    ):
        self.upsert_calls.append(
            {
                "tg_id": tg_id,
                "name": name,
                "username": username,
                "tenant_id": tenant_id,
            }
        )
        return SimpleNamespace(name=name, tg_id=tg_id)

    async def set_panel_message_id(self, chat_id: int, topic_id: int, message_id: int):
        self.set_panel_calls.append((chat_id, topic_id, message_id))

    async def get_or_create_panel_message_id(self, chat_id: int, topic_id: int, message_id: int | None = None):
        return message_id or 900


class _FakeSender:
    def __init__(self) -> None:
        self.answer_calls: list[dict] = []

    async def answer(self, callback, text: str | None = None, show_alert: bool = False):
        self.answer_calls.append({"text": text, "show_alert": show_alert})

    async def get_chat(self, tg_id: int):
        return SimpleNamespace(full_name=f"Manager {tg_id}", username=f"user{tg_id}")

    async def delete_message(self, chat_id: int, message_id: int, thread_id: int | None = None):
        return None


class _FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(data or {})
        self.cleared = False

    async def clear(self) -> None:
        self.cleared = True
        self._data.clear()

    async def get_data(self) -> dict:
        return dict(self._data)

    async def set_state(self, state) -> None:
        return None

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)


class _FakeTenantRepo:
    def __init__(self, tenant):
        self._tenant = tenant

    async def get_by_group_id(self, group_id: int):
        return self._tenant


def _callback(data: str) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=111),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-100500),
            message_id=700,
            message_thread_id=55,
        ),
    )


def _contact_message() -> SimpleNamespace:
    return SimpleNamespace(
        contact=SimpleNamespace(user_id=444, first_name="Scoped", last_name="Manager"),
        forward_origin=None,
        chat=SimpleNamespace(id=-100500),
        message_id=701,
        message_thread_id=55,
    )


def _patch_panel_runtime(monkeypatch, *, repo: _FakeRepo, session: _FakeSession, group_id: int = -100500) -> None:
    monkeypatch.setattr(panel, "AsyncSessionLocal", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(panel, "LeadRepository", lambda session_obj: repo)

    async def _fake_resolve_admin_context(event, tenant, sender):
        return group_id, 111

    async def _fake_resolve_topic_thread_id(*args, **kwargs):
        return 55

    async def _fake_safe_edit(*args, **kwargs):
        return args[4]

    monkeypatch.setattr(panel, "resolve_admin_context", _fake_resolve_admin_context)
    monkeypatch.setattr(panel, "resolve_topic_thread_id", _fake_resolve_topic_thread_id)
    monkeypatch.setattr(panel, "_safe_edit_panel_message", _fake_safe_edit)


def test_panel_team_callback_scopes_managers_by_tenant(monkeypatch) -> None:
    repo = _FakeRepo()
    session = _FakeSession()
    sender = _FakeSender()
    state = _FakeState()
    tenant = SimpleNamespace(id=41, max_managers=-1)

    _patch_panel_runtime(monkeypatch, repo=repo, session=session)

    asyncio.run(
        panel.handle_panel_actions(
            callback=_callback("panel:team"),
            state=state,
            sender=sender,
            tenant=tenant,
        )
    )

    assert repo.get_all_calls == [{"include_inactive": True, "tenant_id": 41}]


def test_team_cancel_callback_scopes_managers_by_tenant(monkeypatch) -> None:
    repo = _FakeRepo()
    session = _FakeSession()
    sender = _FakeSender()
    state = _FakeState()
    tenant = SimpleNamespace(id=42, max_managers=-1)

    _patch_panel_runtime(monkeypatch, repo=repo, session=session)

    asyncio.run(
        panel.handle_panel_actions(
            callback=_callback("team:cancel"),
            state=state,
            sender=sender,
            tenant=tenant,
        )
    )

    assert repo.get_all_calls == [{"include_inactive": True, "tenant_id": 42}]


def test_contact_flow_scopes_upsert_and_manager_list_by_tenant(monkeypatch) -> None:
    repo = _FakeRepo()
    session = _FakeSession()
    sender = _FakeSender()
    state = _FakeState(
        {
            "panel_chat_id": -100500,
            "panel_topic_id": 55,
            "panel_message_id": 700,
        }
    )
    tenant = SimpleNamespace(id=43, max_managers=-1)

    _patch_panel_runtime(monkeypatch, repo=repo, session=session)

    asyncio.run(
        panel.handle_manager_contact(
            message=_contact_message(),
            state=state,
            sender=sender,
            tenant=tenant,
        )
    )

    assert repo.upsert_calls
    assert repo.upsert_calls[0]["tenant_id"] == 43
    assert repo.get_all_calls == [{"include_inactive": True, "tenant_id": 43}]


def test_panel_actions_fail_closed_when_tenant_cannot_be_resolved(monkeypatch) -> None:
    repo = _FakeRepo()
    session = _FakeSession()
    sender = _FakeSender()
    state = _FakeState()

    _patch_panel_runtime(monkeypatch, repo=repo, session=session)
    monkeypatch.setattr(panel, "TenantRepository", lambda session_obj: _FakeTenantRepo(None))

    asyncio.run(
        panel.handle_panel_actions(
            callback=_callback("panel:team"),
            state=state,
            sender=sender,
            tenant=None,
        )
    )

    assert repo.get_all_calls == []
    assert any(call["show_alert"] for call in sender.answer_calls)


def test_panel_source_contains_tenant_scoped_include_inactive_calls() -> None:
    source = Path("app/bot/handlers/panel.py").read_text(encoding="utf-8")
    assert source.count("get_all_managers(include_inactive=True, tenant_id=tenant_id)") >= 3
