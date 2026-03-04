"""reminders: timestamps tz-aware

Revision ID: cdd5c5ba1651
Revises: b35de901ffbc
Create Date: 2026-03-05 01:37:14.993497

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cdd5c5ba1651'
down_revision = 'b35de901ffbc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE reminders ALTER COLUMN remind_at TYPE timestamptz USING remind_at AT TIME ZONE 'UTC';")
    op.execute("ALTER TABLE reminders ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC';")

def downgrade() -> None:
    op.execute("ALTER TABLE reminders ALTER COLUMN remind_at TYPE timestamp USING remind_at AT TIME ZONE 'UTC';")
    op.execute("ALTER TABLE reminders ALTER COLUMN created_at TYPE timestamp USING created_at AT TIME ZONE 'UTC';")