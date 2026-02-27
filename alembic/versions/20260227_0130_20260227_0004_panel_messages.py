"""20260227_0004_panel_messages

Revision ID: 9f2b6f7c1a2b
Revises: eab24a557864
Create Date: 2026-02-27 01:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9f2b6f7c1a2b"
down_revision = "eab24a557864"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "panel_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "topic_id", name="uq_panel_messages_chat_topic"),
    )


def downgrade():
    op.drop_table("panel_messages")
