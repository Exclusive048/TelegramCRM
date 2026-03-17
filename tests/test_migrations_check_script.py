import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts import migrations_check


def test_migrations_check_uses_postgres_default_and_runs_alembic(monkeypatch) -> None:
    monkeypatch.delenv("MIGRATIONS_CHECK_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")

    captured: dict[str, object] = {}

    def _fake_run(cmd, cwd):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(migrations_check.subprocess, "run", _fake_run)

    rc = migrations_check.main()

    assert rc == 0
    assert os.environ["DATABASE_URL"] == migrations_check.DEFAULT_MIGRATIONS_CHECK_DATABASE_URL
    assert captured["cmd"] == [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"]
    assert captured["cwd"] == migrations_check.ROOT


def test_migrations_check_returns_clear_error_for_sqlite_override() -> None:
    env = os.environ.copy()
    env["MIGRATIONS_CHECK_DATABASE_URL"] = "sqlite+aiosqlite:///./_alembic_tmp.db"
    project_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "scripts.migrations_check"],
        cwd=project_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    combined_output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 2
    assert "supports only PostgreSQL dialect in offline SQL mode" in combined_output
    assert "NotImplementedError" not in combined_output
