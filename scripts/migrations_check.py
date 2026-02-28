from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _ensure_env():
    os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")
    os.environ.setdefault("CRM_GROUP_ID", "1")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_alembic_tmp.db")
    os.environ.setdefault("API_SECRET_KEY", "dev")


def main() -> int:
    _ensure_env()
    cmd = [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        print("Migrations check passed.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
