"""Лимиты и настройки тарифных планов."""

PLAN_LIMITS = {
    "trial": {
        "max_leads_per_month": 50,
        "max_managers": 3,
        "sla_new_hours": 2,
        "sla_in_progress_days": 3,
    },
    "base": {
        "max_leads_per_month": -1,  # unlimited
        "max_managers": 5,
        "sla_new_hours": 2,
        "sla_in_progress_days": 3,
    },
    "pro": {
        "max_leads_per_month": -1,
        "max_managers": -1,
        "sla_new_hours": 1,
        "sla_in_progress_days": 2,
    },
}


def get_plan_limits(plan: str) -> dict:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["trial"])
