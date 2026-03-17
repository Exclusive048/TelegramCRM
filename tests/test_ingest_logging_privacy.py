import re
from pathlib import Path


LEADS_PATH = Path("app/api/routes/leads.py")
DEPS_PATH = Path("app/api/deps.py")


def _function_block(source: str, func_name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?def {func_name}\(.*?(?=\n\n(?:\s*(?:async\s+)?def |\s*class |\s*@router|\Z))",
        source,
        flags=re.S,
    )
    if not match:
        raise AssertionError(f"Function block not found: {func_name}")
    return match.group(0)


def test_tilda_webhook_logging_avoids_raw_pii_fields() -> None:
    source = LEADS_PATH.read_text(encoding="utf-8")
    block = _function_block(source, "tilda_webhook")

    forbidden_fragments = [
        "lead_data['name']",
        "lead_data['phone']",
        "lead_data['email']",
        "logger.info(f\"tilda_webhook parsed",
        "logger.debug(f\"tilda_webhook raw fields",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in block


def test_ingest_logging_keeps_technical_context_events() -> None:
    source = LEADS_PATH.read_text(encoding="utf-8")
    create_block = _function_block(source, "create_lead")
    tilda_block = _function_block(source, "tilda_webhook")

    for expected in [
        "ingest_request_received",
        "ingest_create_success",
        "request_id",
        "tenant_id",
        "endpoint",
        "source",
    ]:
        assert expected in create_block
        assert expected in tilda_block

    assert "tilda_payload_parsed" in tilda_block
    assert "payload_key_count" in tilda_block
    assert "known_key_count" in tilda_block
    assert "unknown_key_count" in tilda_block
    assert "contains_unexpected_keys" in tilda_block
    assert "known_key_flags" in tilda_block


def test_ingest_error_paths_have_safe_logging_events() -> None:
    source = LEADS_PATH.read_text(encoding="utf-8")
    atomic_block = _function_block(source, "_create_lead_atomic")
    tilda_block = _function_block(source, "tilda_webhook")

    assert "ingest_quota_denied" in atomic_block
    assert "ingest_create_failed" in atomic_block
    assert "ingest_payload_parse_error" in tilda_block


def test_payload_shape_logger_uses_allowlist_metadata_only() -> None:
    source = LEADS_PATH.read_text(encoding="utf-8")
    block = _function_block(source, "_payload_shape")

    assert ".values(" not in block
    assert "payload_keys" not in block
    assert "known_key_count" in block
    assert "unknown_key_count" in block
    assert "contains_unexpected_keys" in block
    assert "known_key_flags" in block
    assert "has_sensitive_known_keys" in block


def test_ingest_logging_no_arbitrary_input_key_names() -> None:
    source = LEADS_PATH.read_text(encoding="utf-8")
    assert "payload_keys" not in source
    assert "payload_keys_truncated" not in source


def test_auth_denied_logs_do_not_include_api_key_values() -> None:
    source = DEPS_PATH.read_text(encoding="utf-8")
    assert "api_auth_denied" in source

    logger_calls = re.findall(r"logger\.warning\((.*?)\)", source, flags=re.S)
    assert logger_calls
    for call in logger_calls:
        assert "x_api_key" not in call
        assert "x_management_api_key" not in call
