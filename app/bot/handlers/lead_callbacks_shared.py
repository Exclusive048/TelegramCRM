from __future__ import annotations

import re
from datetime import datetime, timezone

from aiogram.fsm.state import State, StatesGroup

from app.db.repositories.lead_repository import LeadRepository

PHONE_RE = re.compile(r"^[+7890][0-9\-\s]{6,19}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class NoteState(StatesGroup):
    waiting_for_text = State()


class RejectState(StatesGroup):
    waiting_for_reason = State()
    waiting_for_custom_reason = State()


class AmountState(StatesGroup):
    waiting_for_amount = State()


class ReminderState(StatesGroup):
    waiting_for_custom_time = State()


class CreateLeadState(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_email = State()
    waiting_for_service = State()
    waiting_for_comment = State()
    waiting_for_confirm = State()


REJECT_REASON_LABELS = {
    "no_budget": "Нет бюджета",
    "no_answer": "Не дозвонились",
    "not_target": "Не целевой",
    "changed_mind": "Передумал",
}
NO_ACCESS_TEXT = "\u26d4\ufe0f \u0423 \u0432\u0430\u0441 \u043d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u043a \u044d\u0442\u043e\u043c\u0443 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044e."


def _get_group_id(tenant) -> int | None:
    return tenant.group_id if tenant else None


def _parse_amount(value: str) -> float | None:
    normalized = value.strip().replace(" ", "").replace(",", ".")
    try:
        amount = float(normalized)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount


def _parse_custom_datetime(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m %H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%d.%m %H:%M":
                parsed = parsed.replace(year=datetime.now(timezone.utc).year)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def _get_manager(repo: LeadRepository, user_id: int, tenant_id: int | None = None):
    return await repo.get_manager_by_tg_id(user_id, tenant_id=tenant_id)


def _manager_can_act(manager, lead) -> bool:
    if manager.is_admin:
        return True
    if lead.manager_id is None:
        return True
    return lead.manager_id == manager.id

