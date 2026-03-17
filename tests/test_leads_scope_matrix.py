from pathlib import Path
import re


LEADS_PATH = Path("app/api/routes/leads.py")


def _function_block(source: str, func_name: str) -> str:
    match = re.search(rf"async def {func_name}\(.*?(?=\n\n(?:@router|# |\Z))", source, flags=re.S)
    if not match:
        raise AssertionError(f"Function block not found: {func_name}")
    return match.group(0)


def test_leads_routes_use_expected_scope_dependencies() -> None:
    source = LEADS_PATH.read_text(encoding="utf-8")

    create_block = _function_block(source, "create_lead")
    assert "Depends(verify_ingest_server_api_key)" in create_block
    assert "_create_lead_atomic(" in create_block

    tilda_block = _function_block(source, "tilda_webhook")
    assert "Depends(verify_ingest_server_api_key)" in tilda_block
    assert "_parse_tilda(data)" in tilda_block
    assert "_create_lead_atomic(" in tilda_block

    get_leads_block = _function_block(source, "get_leads")
    assert "Depends(verify_management_api_key)" in get_leads_block

    get_lead_block = _function_block(source, "get_lead")
    assert "Depends(verify_management_api_key)" in get_lead_block

    update_block = _function_block(source, "update_lead")
    assert "Depends(verify_management_api_key)" in update_block
    assert "if body.status:" not in update_block
    assert 'return JSONResponse(status_code=409, content={"error": "invalid_transition"})' in update_block

    comment_block = _function_block(source, "add_comment")
    assert "Depends(verify_management_api_key)" in comment_block
