"""saas_tenants

Revision ID: c2d3e4f5a6b7
Revises: e4e35b6d792e
Create Date: 2026-03-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c2d3e4f5a6b7"
down_revision = "e4e35b6d792e"
branch_labels = None
depends_on = None


def upgrade():
    # Таблица тенантов
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("group_id", sa.BigInteger, unique=True, nullable=False),
        sa.Column("owner_tg_id", sa.BigInteger, nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("trial_used", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("trial_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscription_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan", sa.String(50), nullable=False, server_default="trial"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tenants_group_id", "tenants", ["group_id"])

    # Таблица платежей
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("yukassa_id", sa.String(100), unique=True, nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("period_days", sa.Integer, nullable=False, server_default="30"),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Поле tenant_id в существующих таблицах
    op.add_column(
        "leads",
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=True),
    )
    op.add_column(
        "managers",
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=True),
    )
    op.create_index("ix_leads_tenant_id", "leads", ["tenant_id"])
    op.create_index("ix_managers_tenant_id", "managers", ["tenant_id"])


def downgrade():
    op.drop_index("ix_managers_tenant_id")
    op.drop_index("ix_leads_tenant_id")
    op.drop_column("managers", "tenant_id")
    op.drop_column("leads", "tenant_id")
    op.drop_table("payments")
    op.drop_index("ix_tenants_group_id")
    op.drop_table("tenants")
