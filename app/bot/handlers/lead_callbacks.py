from __future__ import annotations

from aiogram import Router

from .lead_callbacks_create import router as create_router
from .lead_callbacks_force_reply import router as force_reply_router
from .lead_callbacks_notes import router as notes_router
from .lead_callbacks_shared import (
    AmountState,
    CreateLeadState,
    NoteState,
    RejectState,
    ReminderState,
)
from .lead_callbacks_status import router as status_router

router = Router()
router.include_router(status_router)
router.include_router(notes_router)
router.include_router(force_reply_router)
router.include_router(create_router)

__all__ = [
    "router",
    "AmountState",
    "CreateLeadState",
    "NoteState",
    "RejectState",
    "ReminderState",
]

