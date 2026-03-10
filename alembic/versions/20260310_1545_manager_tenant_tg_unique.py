"""manager tenant+tg unique index

Revision ID: 9f2d4a1b6c3e
Revises: 7ad5f7b8c9d1
Create Date: 2026-03-10 15:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9f2d4a1b6c3e"
down_revision = "7ad5f7b8c9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM managers
            WHERE id IN (
                SELECT id
                FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY tenant_id, tg_id
                               ORDER BY id DESC
                           ) AS rn
                    FROM managers
                ) AS ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )
    op.execute(sa.text("ALTER TABLE managers DROP CONSTRAINT IF EXISTS managers_tg_id_key"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_managers_tg_id"))
    op.create_index("ix_managers_tg_id", "managers", ["tg_id"], unique=False)
    op.create_index(
        "uq_managers_tenant_tg_id",
        "managers",
        ["tenant_id", "tg_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_managers_tenant_tg_id", table_name="managers")
    op.drop_index("ix_managers_tg_id", table_name="managers")
    op.create_index("ix_managers_tg_id", "managers", ["tg_id"], unique=True)
