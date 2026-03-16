import os

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from scripts import migrations_check, smoke


def test_empty_master_admin_env_is_normalized_to_default(monkeypatch) -> None:
    monkeypatch.setenv("MASTER_ADMIN_TG_ID", "")

    settings = Settings(
        _env_file=None,
        bot_token="123:TEST_TOKEN",
        database_url="sqlite+aiosqlite:///./tmp.db",
        public_domain="example.com",
    )

    assert settings.master_admin_tg_id == 0


def test_empty_numeric_env_field_with_default_is_normalized(monkeypatch) -> None:
    monkeypatch.setenv("SUBSCRIPTION_PRICE", "")

    settings = Settings(
        _env_file=None,
        bot_token="123:TEST_TOKEN",
        database_url="sqlite+aiosqlite:///./tmp.db",
        public_domain="example.com",
    )

    assert settings.subscription_price == 990


def test_required_fields_remain_strictly_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///./tmp.db",
            public_domain="example.com",
        )


def test_smoke_env_bootstrap_replaces_blank_numeric_fields(monkeypatch) -> None:
    monkeypatch.setenv("MASTER_ADMIN_TG_ID", "")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("PUBLIC_DOMAIN", "")

    smoke._ensure_env()

    assert os.getenv("MASTER_ADMIN_TG_ID") == "0"
    assert os.getenv("BOT_TOKEN")
    assert os.getenv("DATABASE_URL")
    assert os.getenv("PUBLIC_DOMAIN")

    # Bootstrap settings should not fail after script env normalization.
    settings = Settings(_env_file=None)
    assert settings.master_admin_tg_id == 0


def test_migrations_env_bootstrap_replaces_blank_numeric_fields(monkeypatch) -> None:
    monkeypatch.setenv("MASTER_ADMIN_TG_ID", "")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("PUBLIC_DOMAIN", "")

    migrations_check._ensure_env()

    assert os.getenv("MASTER_ADMIN_TG_ID") == "0"
    assert os.getenv("BOT_TOKEN")
    assert os.getenv("DATABASE_URL")
    assert os.getenv("PUBLIC_DOMAIN")

    settings = Settings(_env_file=None)
    assert settings.master_admin_tg_id == 0
