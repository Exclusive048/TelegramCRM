import asyncio
import json
from types import SimpleNamespace

from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Message, Update
from loguru import logger

from app.bot.diagnostics import reset_trace_context, set_trace_context
from app.bot.middlewares.tracing_middleware import HandlerTraceMiddleware, UpdateTraceMiddleware
from app.bot.utils.callback_parser import safe_parse


def _build_message_update(*, update_id: int = 1, text: str = "/start") -> Update:
    raw = {
        "update_id": update_id,
        "message": {
            "message_id": 101,
            "date": 0,
            "chat": {
                "id": -100123,
                "type": "supergroup",
                "title": "crm",
            },
            "from": {
                "id": 9001,
                "is_bot": False,
                "first_name": "User",
            },
            "text": text,
        },
    }
    return Update.model_validate(raw, context={"bot": SimpleNamespace(id=77)})


def _collect_tg_events():
    events: list[tuple[str, dict]] = []

    def _sink(message) -> None:
        payload = message.record["message"]
        if not payload.startswith("tg_"):
            return
        event, _, raw_json = payload.partition(" ")
        data = json.loads(raw_json) if raw_json else {}
        events.append((event, data))

    sink_id = logger.add(_sink, format="{message}")
    return events, sink_id


class _FakeState:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def get_state(self) -> str | None:
        return self.value


def test_update_trace_generates_trace_id_and_logs_unhandled_outcome() -> None:
    update = _build_message_update(update_id=51, text="/lost")
    state = _FakeState(value=None)
    middleware = UpdateTraceMiddleware(bot_role="crm_bot")
    events, sink_id = _collect_tg_events()

    async def _run() -> None:
        async def _handler(event, data):
            return UNHANDLED

        result = await middleware(_handler, update, {"bot": SimpleNamespace(id=77), "state": state})
        assert result is UNHANDLED

    try:
        asyncio.run(_run())
    finally:
        logger.remove(sink_id)

    event_names = [name for name, _ in events]
    assert "tg_update_received" in event_names
    assert "tg_update_unhandled" in event_names
    assert "tg_update_outcome" in event_names

    received_payload = next(payload for name, payload in events if name == "tg_update_received")
    assert len(received_payload["trace_id"]) == 32

    outcome_payload = next(payload for name, payload in events if name == "tg_update_outcome")
    assert outcome_payload["outcome"] == "unhandled"


def test_update_trace_logs_fsm_state_transition() -> None:
    update = _build_message_update(update_id=61, text="/flow")
    state = _FakeState(value="State:start")
    middleware = UpdateTraceMiddleware(bot_role="crm_bot")
    events, sink_id = _collect_tg_events()

    async def _run() -> None:
        async def _handler(event, data):
            state.value = "State:end"
            return None

        await middleware(_handler, update, {"bot": SimpleNamespace(id=77), "state": state})

    try:
        asyncio.run(_run())
    finally:
        logger.remove(sink_id)

    transitions = [payload for name, payload in events if name == "tg_state_transition"]
    assert transitions
    assert transitions[0]["fsm_state_before"] == "State:start"
    assert transitions[0]["fsm_state_after"] == "State:end"


def test_handler_trace_logs_entry_and_success() -> None:
    update = _build_message_update(update_id=71, text="/setup")
    message = update.event
    assert isinstance(message, Message)
    state = _FakeState(value="Setup:waiting")
    middleware = HandlerTraceMiddleware(bot_role="crm_bot")
    events, sink_id = _collect_tg_events()

    async def _run() -> None:
        async def _actual_handler(event, data):
            return "ok"

        fake_handler_meta = SimpleNamespace(callback=_actual_handler)
        data = {
            "state": state,
            "event_router": SimpleNamespace(name="crm.setup"),
            "handler": fake_handler_meta,
            "bot": SimpleNamespace(id=77),
        }
        result = await middleware(_actual_handler, message, data)
        assert result == "ok"

    try:
        asyncio.run(_run())
    finally:
        logger.remove(sink_id)

    enter = next(payload for name, payload in events if name == "tg_handler_enter")
    success = next(payload for name, payload in events if name == "tg_handler_success")
    assert enter["matched_router"] == "crm.setup"
    assert enter["matched_handler"].endswith("._run.<locals>._actual_handler")
    assert success["matched_router"] == "crm.setup"


def test_callback_parse_failure_keeps_trace_correlation() -> None:
    events, sink_id = _collect_tg_events()
    token = set_trace_context({"trace_id": "trace-abc", "bot_role": "crm_bot"})
    try:
        parsed = safe_parse("lead:bad", expected_parts=3, expected_types=(str, str, int))
        assert parsed is None
    finally:
        reset_trace_context(token)
        logger.remove(sink_id)

    callback_failed = next(payload for name, payload in events if name == "tg_callback_parse_failed")
    assert callback_failed["trace_id"] == "trace-abc"
    assert callback_failed["parse_reason"] in {"unexpected_parts_count", "int_cast_failed"}
