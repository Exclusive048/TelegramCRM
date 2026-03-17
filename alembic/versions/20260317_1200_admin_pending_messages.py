"""add admin pending messages table

Revision ID: f2a1b7c9d4e8
Revises: c7e1a9d4b2f0
Create Date: 2026-03-17 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2a1b7c9d4e8"
down_revision = "c7e1a9d4b2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_pending_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_pending_messages_admin_tg_id",
        "admin_pending_messages",
        ["admin_tg_id"],
        unique=True,
    )
    op.create_index(
        "ix_admin_pending_messages_expires_at",
        "admin_pending_messages",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_pending_messages_expires_at", table_name="admin_pending_messages")
    op.drop_index("ix_admin_pending_messages_admin_tg_id", table_name="admin_pending_messages")
    op.drop_table("admin_pending_messages")
