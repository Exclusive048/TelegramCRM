import asyncio
import re
from pathlib import Path


class _QuotaLimitError(Exception):
    pass


class _QuotaStore:
    def __init__(self, *, limit: int, count: int = 0) -> None:
        self.limit = limit
        self.count = count
        self.lock = asyncio.Lock()

    async def reserve(self, session) -> tuple[bool, int, int]:
        async with self.lock:
            next_count = self.count + session.pending_slots + 1
            if self.limit != -1 and next_count > self.limit:
                return False, self.count + session.pending_slots, self.limit
            session.pending_slots += 1
            return True, next_count, self.limit

    def commit(self, session) -> None:
        self.count += session.pending_slots
        session.pending_slots = 0

    def rollback(self, session) -> None:
        session.pending_slots = 0


class _FakeSession:
    def __init__(self, store: _QuotaStore) -> None:
        self._store = store
        self.pending_slots = 0

    async def commit(self) -> None:
        self._store.commit(self)

    async def rollback(self) -> None:
        self._store.rollback(self)


class _FakeTenantRepository:
    def __init__(self, session: _FakeSession, store: _QuotaStore) -> None:
        self._session = session
        self._store = store

    async def try_reserve_monthly_lead_quota(self, tenant_id: int) -> tuple[bool, int, int]:
        return await self._store.reserve(self._session)


async def _simulate_ingest_transaction(
    *,
    store: _QuotaStore,
    tenant_id: int,
    tenant_limit: int,
    fail_on_create: bool,
) -> None:
    session = _FakeSession(store)
    tenant_repo = _FakeTenantRepository(session, store)
    try:
        if tenant_limit != -1:
            allowed, _, limit = await tenant_repo.try_reserve_monthly_lead_quota(tenant_id)
            if not allowed:
                raise _QuotaLimitError(str(limit))
        if fail_on_create:
            raise RuntimeError("create failed")
        await session.commit()
    except Exception:
        await session.rollback()
        raise


def _function_block(source: str, func_name: str) -> str:
    match = re.search(rf"(?:async\s+)?def {func_name}\(.*?(?=\n\n(?:\s*(?:async\s+)?def |\s*class |\Z))", source, flags=re.S)
    if not match:
        raise AssertionError(f"Function block not found: {func_name}")
    return match.group(0)


def test_parallel_quota_reserve_allows_only_one_when_limit_one() -> None:
    async def scenario() -> None:
        store = _QuotaStore(limit=1, count=0)

        async def attempt() -> bool:
            try:
                await _simulate_ingest_transaction(
                    store=store,
                    tenant_id=1,
                    tenant_limit=1,
                    fail_on_create=False,
                )
                return True
            except _QuotaLimitError:
                return False

        result_a, result_b = await asyncio.gather(attempt(), attempt())
        assert [result_a, result_b].count(True) == 1
        assert store.count == 1

    asyncio.run(scenario())


def test_quota_reservation_rolls_back_on_create_error() -> None:
    async def scenario() -> None:
        store = _QuotaStore(limit=2, count=0)
        try:
            await _simulate_ingest_transaction(
                store=store,
                tenant_id=2,
                tenant_limit=2,
                fail_on_create=True,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected RuntimeError from failed create flow")

        assert store.count == 0

        await _simulate_ingest_transaction(
            store=store,
            tenant_id=2,
            tenant_limit=2,
            fail_on_create=False,
        )
        assert store.count == 1

    asyncio.run(scenario())


def test_leads_route_uses_atomic_quota_reserve_method() -> None:
    source = Path("app/api/routes/leads.py").read_text(encoding="utf-8")
    block = _function_block(source, "_create_lead_atomic")
    assert "try_reserve_monthly_lead_quota(" in block
    assert "increment_leads_count(" not in block


def test_tenant_repository_increment_is_not_read_modify_write() -> None:
    source = Path("app/db/repositories/tenant_repository.py").read_text(encoding="utf-8")
    block = _function_block(source, "increment_leads_count")
    assert "update(Tenant)" in block
    assert ".returning(Tenant.leads_this_month)" in block
    assert "await self.get_by_id(tenant_id)" not in block


def test_tenant_repository_quota_reserve_is_conditional_update() -> None:
    source = Path("app/db/repositories/tenant_repository.py").read_text(encoding="utf-8")
    block = _function_block(source, "try_reserve_monthly_lead_quota")
    assert "next_count <= Tenant.max_leads_per_month" in block
    assert ".returning(Tenant.leads_this_month, Tenant.max_leads_per_month)" in block
