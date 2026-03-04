"""tenant_api_keys_referral

Revision ID: f7a8b9c0d1e2
Revises: c2d3e4f5a6b7
Create Date: 2026-03-04 00:10:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7a8b9c0d1e2"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tenants",
        sa.Column("api_key", sa.String(64), unique=True, nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("referral_code", sa.String(16), unique=True, nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("referred_by_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("referral_bonus_used", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_tenants_api_key", "tenants", ["api_key"])
    op.create_index("ix_tenants_referral_code", "tenants", ["referral_code"])


def downgrade():
    op.drop_index("ix_tenants_referral_code", table_name="tenants")
    op.drop_index("ix_tenants_api_key", table_name="tenants")
    op.drop_column("tenants", "referral_bonus_used")
    op.drop_column("tenants", "referred_by_id")
    op.drop_column("tenants", "referral_code")
    op.drop_column("tenants", "api_key")
