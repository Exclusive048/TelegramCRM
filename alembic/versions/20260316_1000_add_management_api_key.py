"""add management api key

Revision ID: c7e1a9d4b2f0
Revises: b4c8e1d2f9a0
Create Date: 2026-03-16 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c7e1a9d4b2f0"
down_revision = "b4c8e1d2f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("management_api_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_tenants_management_api_key",
        "tenants",
        ["management_api_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_tenants_management_api_key", table_name="tenants")
    op.drop_column("tenants", "management_api_key")
