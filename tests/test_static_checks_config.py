from pathlib import Path


PYPROJECT_PATH = Path("pyproject.toml")


def test_pyproject_enforces_strengthened_static_checks_baseline() -> None:
    source = PYPROJECT_PATH.read_text(encoding="utf-8")

    assert 'select = ["E9", "F", "B"]' in source
    assert '"B008"' in source
    assert '"alembic/versions/*.py" = ["F401"]' in source

    assert "ignore_errors = true" in source
    assert 'module = [' in source
    assert '"app.api.schemas.*"' in source
    assert '"app.api.deps"' in source
    assert '"app.api.routes.leads"' in source
    assert "ignore_errors = false" in source
