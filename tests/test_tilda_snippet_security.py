from pathlib import Path


SNIPPET_PATH = Path("integrations/tilda_snippet.js")


def test_tilda_snippet_has_no_browser_secret() -> None:
    source = SNIPPET_PATH.read_text(encoding="utf-8")

    assert "CRM_API_KEY" not in source
    assert "your_api_secret_key" not in source

    # No direct CRM auth header in browser-side fetch.
    assert "'X-API-Key'" not in source
    assert "\"X-API-Key\"" not in source
