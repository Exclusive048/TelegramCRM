from pathlib import Path
import re


SETUP_PATH = Path("app/bot/handlers/setup.py")


def _cmd_setup_block(source: str) -> str:
    match = re.search(r"async def cmd_setup\(.*?(?=\n\n@router\.message|\Z)", source, flags=re.S)
    if not match:
        raise AssertionError("cmd_setup block not found")
    return match.group(0)


def test_setup_binds_tenant_only_after_guards_and_success_path() -> None:
    source = SETUP_PATH.read_text(encoding="utf-8")
    block = _cmd_setup_block(source)

    bind_idx = block.find("bind_group(")
    assert bind_idx != -1

    required_before_bind = [
        "if not await is_tg_admin",
        "if not getattr(chat, \"is_forum\", False)",
        "_select_setup_tenant(",
        "if not target_tenant:",
        "if not errors:",
    ]
    for token in required_before_bind:
        token_idx = block.find(token)
        assert token_idx != -1
        assert token_idx < bind_idx


def test_setup_keeps_bind_in_success_and_should_bind_branch() -> None:
    source = SETUP_PATH.read_text(encoding="utf-8")
    block = _cmd_setup_block(source)

    assert "if errors and should_bind_tenant:" in block
    assert re.search(
        r"if not errors:\s+.*if should_bind_tenant:\s+.*await repo\.bind_group",
        block,
        flags=re.S,
    )
