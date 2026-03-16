import asyncio
import re
from pathlib import Path

from app.health_checks import run_readiness_checks


class _FakeSession:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def execute(self, _query) -> None:
        if self._fail:
            raise RuntimeError("db unavailable")


class _FakeSessionContext:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def __aenter__(self):
        return _FakeSession(fail=self._fail)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRedis:
    def __init__(self, *, fail_ping: bool) -> None:
        self._fail_ping = fail_ping
        self.closed = False

    async def ping(self) -> None:
        if self._fail_ping:
            raise RuntimeError("redis unavailable")

    async def aclose(self) -> None:
        self.closed = True


def test_readiness_skips_redis_when_disabled() -> None:
    async def scenario() -> None:
        redis_called = {"value": False}

        def session_factory():
            return _FakeSessionContext(fail=False)

        def redis_factory(_url: str):
            redis_called["value"] = True
            return _FakeRedis(fail_ping=False)

        result = await run_readiness_checks(
            use_redis=False,
            redis_url="redis://unused",
            session_factory=session_factory,
            redis_factory=redis_factory,
        )
        assert result["status"] == "ok"
        assert result["dependencies"]["database"] == "ok"
        assert result["dependencies"]["redis"] == "disabled"
        assert redis_called["value"] is False

    asyncio.run(scenario())


def test_readiness_degrades_when_redis_enabled_and_unavailable() -> None:
    async def scenario() -> None:
        def session_factory():
            return _FakeSessionContext(fail=False)

        result = await run_readiness_checks(
            use_redis=True,
            redis_url="redis://required",
            session_factory=session_factory,
            redis_factory=lambda _url: _FakeRedis(fail_ping=True),
        )
        assert result["status"] == "error"
        assert result["code"] == "redis_unavailable"
        assert result["dependencies"]["database"] == "ok"
        assert result["dependencies"]["redis"] == "error"

    asyncio.run(scenario())


def test_readiness_degrades_when_database_is_unavailable() -> None:
    async def scenario() -> None:
        def session_factory():
            return _FakeSessionContext(fail=True)

        result = await run_readiness_checks(
            use_redis=False,
            redis_url="redis://unused",
            session_factory=session_factory,
        )
        assert result["status"] == "error"
        assert result["code"] == "db_unavailable"
        assert result["dependencies"]["database"] == "error"
        assert result["dependencies"]["redis"] == "disabled"

    asyncio.run(scenario())


def test_bootstrap_exposes_live_and_health_alias_to_ready() -> None:
    source = Path("app/bootstrap.py").read_text(encoding="utf-8")

    assert '@app.get("/live")' in source
    assert '@app.get("/ready")' in source
    assert '@app.get("/health")' in source

    health_match = re.search(
        r'@app\.get\("/health"\)\s+async def health\(\):\s+return await readiness\(\)',
        source,
        flags=re.S,
    )
    assert health_match
