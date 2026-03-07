"""restore_lead_card_messages_indexes

Revision ID: ef72cd854497
Revises: 7046132b5a57
Create Date: 2026-03-07 22:52:30.899187

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'ef72cd854497'
down_revision = '7046132b5a57'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_lead_card_messages_chat_message",
        "lead_card_messages",
        ["chat_id", "message_id"],
    )
    op.create_index(
        "ix_lead_card_messages_lead_active",
        "lead_card_messages",
        ["lead_id", "is_active"],
    )


def downgrade():
    op.drop_index("ix_lead_card_messages_lead_active", table_name="lead_card_messages")
    op.drop_index("ix_lead_card_messages_chat_message", table_name="lead_card_messages")
