import asyncio
import os
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")
os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")
os.environ.setdefault("PUBLIC_DOMAIN", "example.com")

_fake_database = types.ModuleType("app.db.database")
_fake_database.AsyncSessionLocal = lambda: None
sys.modules["app.db.database"] = _fake_database

from app.db.models.lead import LeadStatus
from app.services import reminder_service


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _FakeSessionContext:
    def __init__(self, session: _FakeSession):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@dataclass
class _ReminderState:
    reminder_id: int = 501
    lead_id: int = 901
    group_id: int = -100555
    topic_id: int = 77
    retry_count: int = 0
    is_sent: bool = False
    is_processing: bool = False
    cancelled: bool = False
    marked_sent: bool = False
    release_times: list[datetime] | None = None
    refresh_count: int = 0

    def __post_init__(self) -> None:
        if self.release_times is None:
            self.release_times = []

    @property
    def lead(self):
        return SimpleNamespace(
            id=self.lead_id,
            name="Test Lead",
            phone="+70000000000",
            status=LeadStatus.NEW,
            tenant_id=123,
        )


class _FakeReminderRepo:
    def __init__(self, state: _ReminderState):
        self.state = state

    async def claim_reminder_for_delivery(self, reminder_id: int) -> bool:
        if reminder_id != self.state.reminder_id:
            return False
        if self.state.is_sent or self.state.is_processing:
            return False
        self.state.is_processing = True
        self.state.retry_count += 1
        return True

    async def get_reminder_by_id(self, reminder_id: int):
        if reminder_id != self.state.reminder_id:
            return None
        return SimpleNamespace(
            id=self.state.reminder_id,
            lead=self.state.lead,
            is_sent=self.state.is_sent,
            message=None,
            retry_count=self.state.retry_count,
        )

    async def cancel_reminder(self, reminder_id: int) -> bool:
        if reminder_id != self.state.reminder_id:
            return False
        self.state.cancelled = True
        self.state.is_sent = True
        self.state.is_processing = False
        return True

    async def get_group_id_for_lead(self, lead_id: int) -> int | None:
        if lead_id != self.state.lead_id:
            return None
        return self.state.group_id

    async def get_active_card_message(self, lead_id: int):
        return None

    async def release_reminder_after_failure(self, reminder_id: int, *, retry_at: datetime) -> bool:
        if reminder_id != self.state.reminder_id:
            return False
        self.state.release_times.append(retry_at)
        self.state.is_processing = False
        return True

    async def mark_reminder_sent(self, reminder_id: int) -> bool:
        if reminder_id != self.state.reminder_id:
            return False
        self.state.marked_sent = True
        self.state.is_sent = True
        self.state.is_processing = False
        return True


class _FlakySender:
    def __init__(self, *, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def send_message(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("telegram temporary error")
        return SimpleNamespace(message_id=123)


def _patch_runtime(monkeypatch, state: _ReminderState, sender: _FlakySender, scheduled: list[datetime]) -> None:
    fake_session = _FakeSession()
    fake_repo = _FakeReminderRepo(state)

    async def _fake_refresh(repo, sender_obj, lead_id: int, group_id: int | None) -> None:
        state.refresh_count += 1

    async def _fake_resolve_topic_thread_id(chat_id, key, session, sender, thread_id=None):
        return state.topic_id

    def _fake_schedule_job(reminder_id: int, remind_at: datetime, sender_obj, *, group_id, now=None, immediate=False):
        scheduled.append(remind_at)

    monkeypatch.setattr(reminder_service, "AsyncSessionLocal", lambda: _FakeSessionContext(fake_session))
    monkeypatch.setattr(reminder_service, "LeadRepository", lambda session: fake_repo)
    monkeypatch.setattr(reminder_service, "_refresh_lead_card", _fake_refresh)
    monkeypatch.setattr(reminder_service, "resolve_topic_thread_id", _fake_resolve_topic_thread_id)
    monkeypatch.setattr(reminder_service, "_schedule_job", _fake_schedule_job)


def test_retry_backoff_is_stepwise_and_capped() -> None:
    assert reminder_service._retry_delay_seconds(1) == 60
    assert reminder_service._retry_delay_seconds(2) == 300
    assert reminder_service._retry_delay_seconds(3) == 900
    assert reminder_service._retry_delay_seconds(4) == 1800
    assert reminder_service._retry_delay_seconds(40) == 1800


def test_send_failure_before_limit_reschedules_with_backoff(monkeypatch) -> None:
    state = _ReminderState()
    scheduled: list[datetime] = []
    sender = _FlakySender(fail_times=1)
    _patch_runtime(monkeypatch, state, sender, scheduled)

    started_at = datetime.now(timezone.utc)
    asyncio.run(
        reminder_service._send_reminder_job(
            state.reminder_id,
            sender,
            group_id=state.group_id,
        )
    )

    assert state.retry_count == 1
    assert not state.cancelled
    assert not state.marked_sent
    assert len(state.release_times) == 1
    assert len(scheduled) == 1

    retry_delay_sec = (state.release_times[0] - started_at).total_seconds()
    expected = reminder_service._retry_delay_seconds(1)
    assert expected - 5 <= retry_delay_sec <= expected + 5


def test_send_failure_on_max_attempt_abandons_without_reschedule(monkeypatch) -> None:
    state = _ReminderState(retry_count=reminder_service.REMINDER_MAX_DELIVERY_ATTEMPTS - 1)
    scheduled: list[datetime] = []
    sender = _FlakySender(fail_times=1)
    _patch_runtime(monkeypatch, state, sender, scheduled)

    asyncio.run(
        reminder_service._send_reminder_job(
            state.reminder_id,
            sender,
            group_id=state.group_id,
        )
    )

    assert state.retry_count == reminder_service.REMINDER_MAX_DELIVERY_ATTEMPTS
    assert state.cancelled
    assert not state.marked_sent
    assert state.release_times == []
    assert scheduled == []


def test_success_after_transient_failure_completes_cycle(monkeypatch) -> None:
    state = _ReminderState()
    scheduled: list[datetime] = []
    sender = _FlakySender(fail_times=1)
    _patch_runtime(monkeypatch, state, sender, scheduled)

    asyncio.run(
        reminder_service._send_reminder_job(
            state.reminder_id,
            sender,
            group_id=state.group_id,
        )
    )
    asyncio.run(
        reminder_service._send_reminder_job(
            state.reminder_id,
            sender,
            group_id=state.group_id,
        )
    )

    assert state.retry_count == 2
    assert state.marked_sent
    assert not state.cancelled
    assert len(state.release_times) == 1
    assert len(scheduled) == 1
    assert sender.calls == 2
