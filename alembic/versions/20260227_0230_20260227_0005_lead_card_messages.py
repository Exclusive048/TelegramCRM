"""20260227_0005_lead_card_messages

Revision ID: c1b2a3d4e5f6
Revises: 9f2b6f7c1a2b
Create Date: 2026-02-27 02:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c1b2a3d4e5f6"
down_revision = "9f2b6f7c1a2b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_card_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lead_card_messages_lead_active",
        "lead_card_messages",
        ["lead_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_lead_card_messages_chat_message",
        "lead_card_messages",
        ["chat_id", "message_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_lead_card_messages_chat_message", table_name="lead_card_messages")
    op.drop_index("ix_lead_card_messages_lead_active", table_name="lead_card_messages")
    op.drop_table("lead_card_messages")
