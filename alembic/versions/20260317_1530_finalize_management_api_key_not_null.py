"""finalize management_api_key not null

Revision ID: a5d9e1f4c2b7
Revises: f2a1b7c9d4e8
Create Date: 2026-03-17 15:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a5d9e1f4c2b7"
down_revision = "f2a1b7c9d4e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safety pre-step: backfill legacy NULL/blank values in-place before NOT NULL.
    # Uses deterministic per-row value with tenant id to avoid duplicates.
    op.execute(
        sa.text(
            """
            UPDATE tenants
            SET management_api_key = 'mgmt_migr_' || id::text || '_' || substr(md5(id::text || ':' || now()::text), 1, 16)
            WHERE management_api_key IS NULL OR management_api_key = '';
            """
        )
    )
    op.alter_column(
        "tenants",
        "management_api_key",
        existing_type=sa.String(length=64),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "tenants",
        "management_api_key",
        existing_type=sa.String(length=64),
        nullable=True,
    )
