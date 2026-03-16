import os

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

from app.bot.handlers.setup import _select_setup_tenant
from app.db.models.tenant import Tenant


def _tenant(tenant_id: int, group_id: int) -> Tenant:
    return Tenant(
        id=tenant_id,
        group_id=group_id,
        owner_tg_id=1000 + tenant_id,
        company_name=f"Tenant {tenant_id}",
    )


def test_select_setup_tenant_prefers_bound_group() -> None:
    bound = _tenant(1, -100123)
    unbound = _tenant(2, 0)

    selected, error = _select_setup_tenant([unbound, bound], chat_id=-100123)

    assert error is None
    assert selected is bound


def test_select_setup_tenant_uses_single_unbound() -> None:
    unbound = _tenant(1, 0)
    other_group = _tenant(2, -100999)

    selected, error = _select_setup_tenant([unbound, other_group], chat_id=-100123)

    assert error is None
    assert selected is unbound


def test_select_setup_tenant_detects_ambiguous_unbound() -> None:
    selected, error = _select_setup_tenant([_tenant(1, 0), _tenant(2, 0)], chat_id=-100123)

    assert selected is None
    assert error == "ambiguous_unbound"


def test_select_setup_tenant_detects_conflict_same_group() -> None:
    selected, error = _select_setup_tenant(
        [_tenant(1, -100123), _tenant(2, -100123)],
        chat_id=-100123,
    )

    assert selected is None
    assert error == "conflict_same_group"


def test_select_setup_tenant_returns_not_found() -> None:
    selected, error = _select_setup_tenant([_tenant(1, -100111)], chat_id=-100123)

    assert selected is None
    assert error == "not_found"
