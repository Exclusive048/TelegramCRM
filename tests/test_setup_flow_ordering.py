from pathlib import Path
import re


SETUP_PATH = Path("app/bot/handlers/setup.py")


def _cmd_setup_block(source: str) -> str:
    match = re.search(r"async def cmd_setup\(.*?(?=\n\n@router\.message|\Z)", source, flags=re.S)
    if not match:
        raise AssertionError("cmd_setup block not found")
    return match.group(0)


def _cb_setup_select_block(source: str) -> str:
    match = re.search(r"async def cb_setup_select_tenant\(.*?(?=\n\n@router\.message|\Z)", source, flags=re.S)
    if not match:
        raise AssertionError("cb_setup_select_tenant block not found")
    return match.group(0)


def test_setup_command_routes_through_common_execution_path() -> None:
    source = SETUP_PATH.read_text(encoding="utf-8")
    block = _cmd_setup_block(source)

    assert "_ensure_setup_prerequisites(" in block
    assert "_select_setup_tenant(" in block
    assert "_run_setup_for_tenant(" in block
    assert "_build_setup_selection_markup(" in block
    assert '"tg_setup_selection_shown"' in block


def test_setup_selection_callback_rechecks_guards_and_uses_common_execution_path() -> None:
    source = SETUP_PATH.read_text(encoding="utf-8")
    block = _cb_setup_select_block(source)

    assert "_ensure_setup_prerequisites(" in block
    assert "tenant.owner_tg_id != from_user.id" in block
    assert "tenant.group_id != 0" in block
    assert "_run_setup_for_tenant(" in block
