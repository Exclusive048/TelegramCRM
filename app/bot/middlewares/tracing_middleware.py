from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import TelegramObject, Update

from app.bot.diagnostics import (
    TG_HANDLER_ENTER,
    TG_HANDLER_FAILED,
    TG_HANDLER_SUCCESS,
    TG_MIDDLEWARE_ENTER,
    TG_MIDDLEWARE_EXIT,
    TG_STATE_TRANSITION,
    TG_UPDATE_OUTCOME,
    TG_UPDATE_RECEIVED,
    TG_UPDATE_UNHANDLED,
    TRACE_CONTEXT_KEY,
    create_update_trace_context,
    emit_tg_event,
    ensure_trace_context,
    get_fsm_state,
    mark_update_outcome,
    reset_trace_context,
    resolve_handler_identity,
    set_trace_context,
    update_trace_context,
)


class UpdateTraceMiddleware(BaseMiddleware):
    def __init__(self, *, bot_role: str) -> None:
        self._bot_role = bot_role

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        context = create_update_trace_context(event, data, bot_role=self._bot_role)
        data["trace_id"] = context["trace_id"]
        data[TRACE_CONTEXT_KEY] = context
        token = set_trace_context(context)

        state_before = await get_fsm_state(data)
        started_at = time.perf_counter()

        emit_tg_event(TG_UPDATE_RECEIVED, fsm_state_before=state_before)
        emit_tg_event(TG_MIDDLEWARE_ENTER, middleware="update_trace")

        response: Any = UNHANDLED
        try:
            response = await handler(event, data)
            if response is UNHANDLED:
                mark_update_outcome("unhandled")
            elif context.get("outcome") is None:
                mark_update_outcome("handled")
            return response
        except Exception as exc:
            mark_update_outcome("failed", rejection_reason=type(exc).__name__)
            emit_tg_event(
                TG_HANDLER_FAILED,
                level="ERROR",
                scope="update",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            state_after = await get_fsm_state(data)
            if state_before != state_after:
                emit_tg_event(
                    TG_STATE_TRANSITION,
                    transition_scope="update",
                    fsm_state_before=state_before,
                    fsm_state_after=state_after,
                )

            outcome = context.get("outcome") or ("unhandled" if response is UNHANDLED else "handled")
            rejection_reason = context.get("rejection_reason")
            duration_ms = int((time.perf_counter() - started_at) * 1000)

            if outcome == "unhandled":
                emit_tg_event(TG_UPDATE_UNHANDLED)

            emit_tg_event(
                TG_MIDDLEWARE_EXIT,
                middleware="update_trace",
                outcome=outcome,
                rejection_reason=rejection_reason,
                duration_ms=duration_ms,
            )
            emit_tg_event(
                TG_UPDATE_OUTCOME,
                outcome=outcome,
                rejection_reason=rejection_reason,
                duration_ms=duration_ms,
                fsm_state_before=state_before,
                fsm_state_after=state_after,
            )
            reset_trace_context(token)


class HandlerTraceMiddleware(BaseMiddleware):
    def __init__(self, *, bot_role: str) -> None:
        self._bot_role = bot_role

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        _, token = ensure_trace_context(data, bot_role=self._bot_role, event=event)
        router_name, handler_name = resolve_handler_identity(data)
        update_trace_fields = {}
        if router_name:
            update_trace_fields["matched_router"] = router_name
        if handler_name:
            update_trace_fields["matched_handler"] = handler_name
        if update_trace_fields:
            update_trace_context(**update_trace_fields)

        state_before = await get_fsm_state(data)
        emit_tg_event(
            TG_HANDLER_ENTER,
            matched_router=router_name,
            matched_handler=handler_name,
            fsm_state_before=state_before,
        )
        try:
            result = await handler(event, data)
        except Exception as exc:
            mark_update_outcome("failed", rejection_reason=type(exc).__name__)
            emit_tg_event(
                TG_HANDLER_FAILED,
                level="ERROR",
                matched_router=router_name,
                matched_handler=handler_name,
                error_type=type(exc).__name__,
            )
            raise
        else:
            state_after = await get_fsm_state(data)
            if state_before != state_after:
                emit_tg_event(
                    TG_STATE_TRANSITION,
                    transition_scope="handler",
                    matched_router=router_name,
                    matched_handler=handler_name,
                    fsm_state_before=state_before,
                    fsm_state_after=state_after,
                )
            emit_tg_event(
                TG_HANDLER_SUCCESS,
                matched_router=router_name,
                matched_handler=handler_name,
                fsm_state_before=state_before,
                fsm_state_after=state_after,
            )
            return result
        finally:
            if token is not None:
                reset_trace_context(token)
