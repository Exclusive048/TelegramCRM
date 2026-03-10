"""security tenant admin + owner trial guard

Revision ID: 7ad5f7b8c9d1
Revises: 1f4b9c2d7a11
Create Date: 2026-03-10 13:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7ad5f7b8c9d1"
down_revision = "1f4b9c2d7a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managers",
        sa.Column(
            "owner_trial_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Backfill managers.tenant_id for legacy rows where it is NULL (best effort).
    op.execute(
        sa.text(
            """
            UPDATE managers AS m
            SET tenant_id = src.tenant_id
            FROM (
                SELECT m2.id AS manager_id, MIN(t.id) AS tenant_id
                FROM managers AS m2
                JOIN tenants AS t ON t.owner_tg_id = m2.tg_id
                WHERE m2.tenant_id IS NULL
                GROUP BY m2.id
            ) AS src
            WHERE m.id = src.manager_id
              AND m.tenant_id IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE managers AS m
            SET tenant_id = src.tenant_id
            FROM (
                SELECT l.manager_id AS manager_id, MAX(l.tenant_id) AS tenant_id
                FROM leads AS l
                WHERE l.manager_id IS NOT NULL
                  AND l.tenant_id IS NOT NULL
                GROUP BY l.manager_id
            ) AS src
            WHERE m.id = src.manager_id
              AND m.tenant_id IS NULL
            """
        )
    )

    # Backfill owner_trial_used for owners that already consumed trial on any tenant.
    op.execute(
        sa.text(
            """
            UPDATE managers AS m
            SET owner_trial_used = TRUE
            WHERE EXISTS (
                SELECT 1
                FROM tenants AS t
                WHERE t.owner_tg_id = m.tg_id
                  AND t.trial_used = TRUE
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("managers", "owner_trial_used")
