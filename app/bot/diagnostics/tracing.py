from __future__ import annotations

import contextvars
import json
import re
import uuid
from typing import Any, Mapping, MutableMapping, Sequence

from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from loguru import logger

TG_UPDATE_RECEIVED = "tg_update_received"
TG_MIDDLEWARE_ENTER = "tg_middleware_enter"
TG_MIDDLEWARE_EXIT = "tg_middleware_exit"
TG_GUARD_REJECTED = "tg_guard_rejected"
TG_HANDLER_ENTER = "tg_handler_enter"
TG_HANDLER_SUCCESS = "tg_handler_success"
TG_HANDLER_FAILED = "tg_handler_failed"
TG_STATE_TRANSITION = "tg_state_transition"
TG_CALLBACK_PARSE_FAILED = "tg_callback_parse_failed"
TG_UPDATE_UNHANDLED = "tg_update_unhandled"
TG_UPDATE_OUTCOME = "tg_update_outcome"

TRACE_CONTEXT_KEY = "tg_trace_context"

_TRACE_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "tg_trace_context",
    default=None,
)
_DEFAULT_EVENT_KEYS: tuple[str, ...] = (
    "trace_id",
    "update_id",
    "bot_role",
    "bot_id",
    "chat_id",
    "user_id",
    "message_id",
    "update_kind",
    "callback_data_preview",
    "text_preview",
    "matched_router",
    "matched_handler",
)
_MAX_TEXT_PREVIEW = 120
_MAX_CALLBACK_PREVIEW = 120
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{7,}\d")
_LONG_DIGITS_RE = re.compile(r"\b\d{6,}\b")
_SECRETISH_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def _compact_whitespace(value: str) -> str:
    return " ".join(value.split())


def _trim(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}…"


def safe_text_preview(text: str | None) -> str | None:
    if not text:
        return None
    normalized = _compact_whitespace(text.strip())
    if not normalized:
        return None
    normalized = _EMAIL_RE.sub("<email>", normalized)
    normalized = _PHONE_RE.sub("<phone>", normalized)
    normalized = _LONG_DIGITS_RE.sub("<digits>", normalized)
    normalized = _SECRETISH_RE.sub("<secret>", normalized)
    return _trim(normalized, limit=_MAX_TEXT_PREVIEW)


def safe_callback_preview(callback_data: str | None) -> str | None:
    if not callback_data:
        return None
    normalized = _compact_whitespace(callback_data.strip())
    if not normalized:
        return None
    normalized = _SECRETISH_RE.sub("<secret>", normalized)
    return _trim(normalized, limit=_MAX_CALLBACK_PREVIEW)


def _resolve_update_kind(event_update: Update) -> str:
    try:
        return event_update.event_type
    except Exception:
        return "unknown"


def extract_event_fields(
    event: TelegramObject | None,
    *,
    update_kind: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "update_kind": update_kind,
        "chat_id": None,
        "user_id": None,
        "message_id": None,
        "callback_data_preview": None,
        "text_preview": None,
    }
    if isinstance(event, Message):
        fields["chat_id"] = event.chat.id
        fields["user_id"] = event.from_user.id if event.from_user else None
        fields["message_id"] = event.message_id
        fields["text_preview"] = safe_text_preview(event.text or event.caption)
        return fields

    if isinstance(event, CallbackQuery):
        fields["user_id"] = event.from_user.id if event.from_user else None
        fields["callback_data_preview"] = safe_callback_preview(event.data)
        message = event.message if isinstance(event.message, Message) else None
        if message:
            fields["chat_id"] = message.chat.id
            fields["message_id"] = message.message_id
            fields["text_preview"] = safe_text_preview(message.text or message.caption)
        return fields

    if event is None:
        return fields

    message_id = getattr(event, "message_id", None)
    if isinstance(message_id, int):
        fields["message_id"] = message_id

    chat = getattr(event, "chat", None)
    chat_id = getattr(chat, "id", None)
    if isinstance(chat_id, int):
        fields["chat_id"] = chat_id

    from_user = getattr(event, "from_user", None)
    user_id = getattr(from_user, "id", None)
    if isinstance(user_id, int):
        fields["user_id"] = user_id

    event_text = getattr(event, "text", None) or getattr(event, "caption", None)
    if isinstance(event_text, str):
        fields["text_preview"] = safe_text_preview(event_text)

    return fields


def create_update_trace_context(
    event_update: Update,
    data: Mapping[str, Any],
    *,
    bot_role: str,
) -> dict[str, Any]:
    update_kind = _resolve_update_kind(event_update)
    event = getattr(event_update, "event", None)
    context: dict[str, Any] = {
        "trace_id": uuid.uuid4().hex,
        "update_id": event_update.update_id,
        "bot_role": bot_role,
        "bot_id": getattr(data.get("bot"), "id", None),
        "matched_router": None,
        "matched_handler": None,
        "outcome": None,
        "rejection_reason": None,
    }
    context.update(extract_event_fields(event, update_kind=update_kind))
    return context


def get_trace_context() -> dict[str, Any] | None:
    return _TRACE_CONTEXT.get()


def has_trace_context() -> bool:
    return get_trace_context() is not None


def set_trace_context(context: dict[str, Any]) -> contextvars.Token:
    return _TRACE_CONTEXT.set(context)


def reset_trace_context(token: contextvars.Token) -> None:
    _TRACE_CONTEXT.reset(token)


def ensure_trace_context(
    data: MutableMapping[str, Any] | None,
    *,
    bot_role: str,
    event: TelegramObject | None = None,
) -> tuple[dict[str, Any], contextvars.Token | None]:
    context = get_trace_context()
    if context is not None:
        return context, None

    context = {
        "trace_id": uuid.uuid4().hex,
        "bot_role": bot_role,
        "update_id": None,
        "bot_id": None,
        "matched_router": None,
        "matched_handler": None,
        "outcome": None,
        "rejection_reason": None,
    }
    context.update(extract_event_fields(event))
    if data is not None:
        bot = data.get("bot")
        if bot is not None:
            context["bot_id"] = getattr(bot, "id", None)
        data["trace_id"] = context["trace_id"]
        data[TRACE_CONTEXT_KEY] = context

    token = set_trace_context(context)
    return context, token


def update_trace_context(**fields: Any) -> None:
    context = get_trace_context()
    if context is None:
        return
    for key, value in fields.items():
        if value is None:
            continue
        context[key] = value


def mark_update_outcome(outcome: str, *, rejection_reason: str | None = None, **fields: Any) -> None:
    context = get_trace_context()
    if context is None:
        return
    context["outcome"] = outcome
    if rejection_reason:
        context["rejection_reason"] = rejection_reason
    for key, value in fields.items():
        if value is None:
            continue
        context[key] = value


def log_guard_rejected(reason: str, **fields: Any) -> None:
    mark_update_outcome("rejected", rejection_reason=reason)
    emit_tg_event(
        TG_GUARD_REJECTED,
        rejection_reason=reason,
        **fields,
    )


def log_callback_parse_failed(
    *,
    raw_data: Any,
    reason: str,
    expected_parts: int,
    expected_types: Sequence[type],
) -> None:
    expected_type_names = [tp.__name__ for tp in expected_types]
    callback_data = raw_data if isinstance(raw_data, str) else None
    log_guard_rejected(
        "callback_parse_failed",
        callback_data_preview=safe_callback_preview(callback_data),
        parse_reason=reason,
        expected_parts=expected_parts,
        expected_types=expected_type_names,
    )
    emit_tg_event(
        TG_CALLBACK_PARSE_FAILED,
        callback_data_preview=safe_callback_preview(callback_data),
        parse_reason=reason,
        expected_parts=expected_parts,
        expected_types=expected_type_names,
    )


def resolve_handler_identity(data: Mapping[str, Any]) -> tuple[str | None, str | None]:
    router_name = getattr(data.get("event_router"), "name", None)
    handler_obj = data.get("handler")
    callback = getattr(handler_obj, "callback", None)
    if callback is None:
        return router_name, None
    module = getattr(callback, "__module__", None)
    qualname = getattr(callback, "__qualname__", None) or getattr(callback, "__name__", None)
    if module and qualname:
        return router_name, f"{module}.{qualname}"
    if qualname:
        return router_name, str(qualname)
    return router_name, None


async def get_fsm_state(data: Mapping[str, Any]) -> str | None:
    state = data.get("state")
    if state is None:
        return None
    get_state = getattr(state, "get_state", None)
    if not callable(get_state):
        return None
    try:
        value = await get_state()
    except Exception:
        return None
    return value


def emit_tg_event(event_name: str, *, level: str = "INFO", **fields: Any) -> None:
    payload: dict[str, Any] = {}
    context = get_trace_context()
    if context:
        for key in _DEFAULT_EVENT_KEYS:
            value = context.get(key)
            if value is not None:
                payload[key] = value

    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = value

    logger.log(
        level.upper(),
        "{} {}",
        event_name,
        json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")),
    )
