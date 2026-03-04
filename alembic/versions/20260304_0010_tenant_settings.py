"""tenant_settings: sla, limits, onboarding

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-03-04 00:10:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenants", sa.Column("onboarding_completed",
        sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("tenants", sa.Column("sla_new_hours",
        sa.Integer(), nullable=False, server_default="2"))
    op.add_column("tenants", sa.Column("sla_in_progress_days",
        sa.Integer(), nullable=False, server_default="3"))
    op.add_column("tenants", sa.Column("max_leads_per_month",
        sa.Integer(), nullable=False, server_default="50"))
    op.add_column("tenants", sa.Column("max_managers",
        sa.Integer(), nullable=False, server_default="3"))
    op.add_column("tenants", sa.Column("leads_this_month",
        sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tenants", sa.Column("leads_month_reset_at",
        sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("expiry_notified_at",
        sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for col in ["onboarding_completed", "sla_new_hours", "sla_in_progress_days",
                "max_leads_per_month", "max_managers", "leads_this_month",
                "leads_month_reset_at", "expiry_notified_at"]:
        op.drop_column("tenants", col)
