from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _set_if_blank(name: str, value: str) -> None:
    current = os.getenv(name)
    if current is None or not current.strip():
        os.environ[name] = value


def _ensure_env():
    _set_if_blank("BOT_TOKEN", "123:TEST_TOKEN")
    _set_if_blank("CRM_GROUP_ID", "1")
    _set_if_blank("DATABASE_URL", "sqlite+aiosqlite:///./_alembic_tmp.db")
    _set_if_blank("PUBLIC_DOMAIN", "example.com")
    _set_if_blank("MASTER_ADMIN_TG_ID", "0")
    _set_if_blank("API_SECRET_KEY", "dev")


def main() -> int:
    _ensure_env()
    cmd = [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        print("Migrations check passed.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
