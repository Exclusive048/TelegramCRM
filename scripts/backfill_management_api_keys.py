from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from app.db.database import AsyncSessionLocal
from app.db.repositories.tenant_repository import TenantRepository


@dataclass
class BackfillResult:
    missing_before: int
    processed: int
    remaining: int
    dry_run: bool
    limit: int | None


async def backfill_management_api_keys(
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> BackfillResult:
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant_ids = await repo.get_tenant_ids_without_management_api_key(limit=limit)
        missing_before = len(tenant_ids)

        processed = 0
        if not dry_run:
            for tenant_id in tenant_ids:
                await repo.ensure_management_api_key(tenant_id)
                processed += 1
            await session.commit()

        remaining = await repo.count_without_management_api_key()

    return BackfillResult(
        missing_before=missing_before,
        processed=processed,
        remaining=remaining,
        dry_run=dry_run,
        limit=limit,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing management_api_key for existing tenants.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report tenants missing management_api_key without writing changes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only first N tenants without management_api_key.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = asyncio.run(
        backfill_management_api_keys(
            dry_run=args.dry_run,
            limit=args.limit,
        )
    )

    print(
        "Backfill management_api_key: "
        f"missing_before={result.missing_before}, "
        f"processed={result.processed}, "
        f"remaining={result.remaining}, "
        f"dry_run={result.dry_run}, "
        f"limit={result.limit}"
    )

    if result.dry_run:
        return 0
    if result.limit is not None:
        return 0
    if result.remaining != 0:
        print("Backfill incomplete: some tenants still have empty management_api_key.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
