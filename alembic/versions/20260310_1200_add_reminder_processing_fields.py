"""add reminder processing fields

Revision ID: 1f4b9c2d7a11
Revises: ef72cd854497
Create Date: 2026-03-10 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1f4b9c2d7a11"
down_revision = "ef72cd854497"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column("is_processing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "reminders",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reminders",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("reminders", "is_processing", server_default=None)
    op.alter_column("reminders", "retry_count", server_default=None)


def downgrade() -> None:
    op.drop_column("reminders", "retry_count")
    op.drop_column("reminders", "processing_started_at")
    op.drop_column("reminders", "is_processing")
