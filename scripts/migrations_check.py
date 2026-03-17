from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MIGRATIONS_CHECK_DATABASE_URL = "postgresql://user:pass@localhost:5432/db"


def _set_if_blank(name: str, value: str) -> None:
    current = os.getenv(name)
    if current is None or not current.strip():
        os.environ[name] = value


def _resolve_migrations_check_database_url() -> str:
    raw_value = os.getenv("MIGRATIONS_CHECK_DATABASE_URL", DEFAULT_MIGRATIONS_CHECK_DATABASE_URL)
    normalized = raw_value.strip()
    if not normalized:
        return DEFAULT_MIGRATIONS_CHECK_DATABASE_URL
    return normalized


def _is_postgres_database_url(database_url: str) -> bool:
    return database_url.startswith(("postgresql://", "postgresql+", "postgres://"))


def _ensure_env() -> str:
    _set_if_blank("BOT_TOKEN", "123:TEST_TOKEN")
    _set_if_blank("CRM_GROUP_ID", "1")
    _set_if_blank("PUBLIC_DOMAIN", "example.com")
    _set_if_blank("MASTER_ADMIN_TG_ID", "0")
    _set_if_blank("API_SECRET_KEY", "dev")
    migrations_database_url = _resolve_migrations_check_database_url()
    # migrations_check validates SQL generation for the production dialect;
    # keep it independent from developer-local DATABASE_URL values.
    os.environ["DATABASE_URL"] = migrations_database_url
    return migrations_database_url


def main() -> int:
    database_url = _ensure_env()
    if not _is_postgres_database_url(database_url):
        print("Migrations check supports only PostgreSQL dialect in offline SQL mode.")
        print(f"Got MIGRATIONS_CHECK_DATABASE_URL={database_url!r}")
        print("Set MIGRATIONS_CHECK_DATABASE_URL to a PostgreSQL URL.")
        return 2

    cmd = [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        print("Migrations check passed.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
