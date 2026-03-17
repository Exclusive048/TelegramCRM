from pathlib import Path

from app.db.models.tenant import Tenant


TENANT_REPO_PATH = Path("app/db/repositories/tenant_repository.py")
FINALIZE_MIGRATION_PATH = Path(
    "alembic/versions/20260317_1530_finalize_management_api_key_not_null.py"
)


def test_tenant_model_marks_management_api_key_not_null() -> None:
    column = Tenant.__table__.c.management_api_key
    assert column.nullable is False


def test_tenant_repository_missing_key_checks_handle_null_and_blank() -> None:
    source = TENANT_REPO_PATH.read_text(encoding="utf-8")
    assert "Tenant.management_api_key.is_(None)" in source
    assert 'Tenant.management_api_key == ""' in source


def test_finalize_migration_backfills_and_sets_not_null() -> None:
    source = FINALIZE_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "UPDATE tenants" in source
    assert "WHERE management_api_key IS NULL OR management_api_key = ''" in source
    assert 'op.alter_column(\n        "tenants",\n        "management_api_key"' in source
    assert "nullable=False" in source
