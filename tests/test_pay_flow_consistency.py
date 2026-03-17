from pathlib import Path
import re


SETUP_PATH = Path("app/bot/handlers/setup.py")
MW_PATH = Path("app/bot/middlewares/tenant_middleware.py")
ENTRYPOINT_PATH = Path("app/entrypoints/crm_bot.py")


def _cmd_pay_block(source: str) -> str:
    match = re.search(r"async def cmd_pay\(.*?(?=\n\n@router\.message|\Z)", source, flags=re.S)
    if not match:
        raise AssertionError("cmd_pay block not found")
    return match.group(0)


def test_pay_handler_registered_and_uses_real_payment_flow() -> None:
    source = SETUP_PATH.read_text(encoding="utf-8")
    assert '@router.message(Command("pay"))' in source

    block = _cmd_pay_block(source)
    assert "repo.get_by_group_id(message.chat.id)" in block
    assert "payment_url = await _create_yukassa_payment(tenant.id)" in block
    assert "reply_markup=_build_payment_markup(payment_url)" in block


def test_pay_command_registered_in_crm_menu() -> None:
    source = ENTRYPOINT_PATH.read_text(encoding="utf-8")
    assert 'BotCommand(command="pay", description="Оплатить или продлить подписку")' in source


def test_pay_ux_copy_is_consistent_with_registration_and_middleware_bypass() -> None:
    setup_source = SETUP_PATH.read_text(encoding="utf-8")
    middleware_source = MW_PATH.read_text(encoding="utf-8")
    entrypoint_source = ENTRYPOINT_PATH.read_text(encoding="utf-8")

    assert "/pay" in setup_source
    assert "/pay" in middleware_source
    assert '"/pay"' in middleware_source
    assert '@router.message(Command("pay"))' in setup_source
    assert 'BotCommand(command="pay"' in entrypoint_source
