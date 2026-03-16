import os

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

from scripts import backfill_management_api_keys as backfill_script


class _FakeSessionContext:
    def __init__(self, repo):
        self.repo = repo
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.committed = True


class _FakeRepo:
    def __init__(self, missing_ids: list[int]):
        self.missing = set(missing_ids)
        self.ensure_calls: list[int] = []

    async def get_tenant_ids_without_management_api_key(self, *, limit=None):
        ids = sorted(self.missing)
        if limit is None:
            return ids
        return ids[:limit]

    async def ensure_management_api_key(self, tenant_id: int):
        self.ensure_calls.append(tenant_id)
        self.missing.discard(tenant_id)
        return f"key-{tenant_id}"

    async def count_without_management_api_key(self):
        return len(self.missing)


def test_backfill_dry_run_does_not_mutate(monkeypatch) -> None:
    repo = _FakeRepo([1, 2, 3])
    session_ctx = _FakeSessionContext(repo)

    monkeypatch.setattr(backfill_script, "AsyncSessionLocal", lambda: session_ctx)
    monkeypatch.setattr(backfill_script, "TenantRepository", lambda session: repo)

    result = backfill_script.asyncio.run(
        backfill_script.backfill_management_api_keys(dry_run=True)
    )

    assert result.missing_before == 3
    assert result.processed == 0
    assert result.remaining == 3
    assert repo.ensure_calls == []
    assert session_ctx.committed is False


def test_backfill_processes_all_missing_keys(monkeypatch) -> None:
    repo = _FakeRepo([10, 20])
    session_ctx = _FakeSessionContext(repo)

    monkeypatch.setattr(backfill_script, "AsyncSessionLocal", lambda: session_ctx)
    monkeypatch.setattr(backfill_script, "TenantRepository", lambda session: repo)

    result = backfill_script.asyncio.run(
        backfill_script.backfill_management_api_keys(dry_run=False)
    )

    assert result.missing_before == 2
    assert result.processed == 2
    assert result.remaining == 0
    assert repo.ensure_calls == [10, 20]
    assert session_ctx.committed is True


def test_backfill_limit_processes_subset(monkeypatch) -> None:
    repo = _FakeRepo([1, 2, 3])
    session_ctx = _FakeSessionContext(repo)

    monkeypatch.setattr(backfill_script, "AsyncSessionLocal", lambda: session_ctx)
    monkeypatch.setattr(backfill_script, "TenantRepository", lambda session: repo)

    result = backfill_script.asyncio.run(
        backfill_script.backfill_management_api_keys(dry_run=False, limit=1)
    )

    assert result.missing_before == 1
    assert result.processed == 1
    assert result.remaining == 2
    assert repo.ensure_calls == [1]
    assert session_ctx.committed is True
