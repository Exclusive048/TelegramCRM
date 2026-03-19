"""add lead archives table

Revision ID: d8f1a2b3c4d5
Revises: a5d9e1f4c2b7
Create Date: 2026-03-19 10:15:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "d8f1a2b3c4d5"
down_revision = "a5d9e1f4c2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    lead_status_enum = postgresql.ENUM(
        "new",
        "in_progress",
        "paid",
        "success",
        "rejected",
        name="leadstatus",
        create_type=False,
    )

    op.create_table(
        "lead_archives",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_lead_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_topic_id", sa.Integer(), nullable=True),
        sa.Column("tg_message_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("service", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("manager_id", sa.Integer(), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("utm_campaign", sa.String(length=255), nullable=True),
        sa.Column("utm_source", sa.String(length=255), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("lead_created_at", sa.DateTime(), nullable=False),
        sa.Column("lead_closed_at", sa.DateTime(), nullable=True),
        sa.Column("final_status", lead_status_enum, nullable=False),
        sa.Column("status_history", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_lead_id", name="uq_lead_archives_source_lead_id"),
    )
    op.create_index("ix_lead_archives_tenant_id", "lead_archives", ["tenant_id"], unique=False)
    op.create_index("ix_lead_archives_lead_closed_at", "lead_archives", ["lead_closed_at"], unique=False)
    op.create_index("ix_lead_archives_final_status", "lead_archives", ["final_status"], unique=False)
    op.create_index("ix_lead_archives_archived_at", "lead_archives", ["archived_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_lead_archives_archived_at", table_name="lead_archives")
    op.drop_index("ix_lead_archives_final_status", table_name="lead_archives")
    op.drop_index("ix_lead_archives_lead_closed_at", table_name="lead_archives")
    op.drop_index("ix_lead_archives_tenant_id", table_name="lead_archives")
    op.drop_table("lead_archives")
