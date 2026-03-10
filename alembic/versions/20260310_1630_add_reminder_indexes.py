"""add reminder indexes

Revision ID: b4c8e1d2f9a0
Revises: 9f2d4a1b6c3e
Create Date: 2026-03-10 16:30:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "b4c8e1d2f9a0"
down_revision = "9f2d4a1b6c3e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_reminders_sent_processing_remind_at",
        "reminders",
        ["is_sent", "is_processing", "remind_at"],
        unique=False,
    )
    op.create_index(
        "ix_reminders_lead_id",
        "reminders",
        ["lead_id"],
        unique=False,
    )
    op.create_index(
        "ix_reminders_manager_tg_id_is_sent",
        "reminders",
        ["manager_tg_id", "is_sent"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_reminders_manager_tg_id_is_sent", table_name="reminders")
    op.drop_index("ix_reminders_lead_id", table_name="reminders")
    op.drop_index("ix_reminders_sent_processing_remind_at", table_name="reminders")
