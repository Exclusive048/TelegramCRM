"""20260227_0006_pipeline_fields

Revision ID: f2c3d4e5f6a7
Revises: c1b2a3d4e5f6
Create Date: 2026-02-27 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2c3d4e5f6a7"
down_revision = "c1b2a3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns
    op.add_column("leads", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column("leads", sa.Column("amount", sa.Numeric(10, 2), nullable=True))
    op.add_column("leads", sa.Column("closed_at", sa.DateTime(), nullable=True))

    # Update enum values (handle both legacy 'closed' and fresh schemas)
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS ("
        "SELECT 1 FROM pg_enum e "
        "JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = 'leadstatus' AND e.enumlabel = 'closed'"
        ") THEN "
        "ALTER TYPE leadstatus RENAME VALUE 'closed' TO 'success'; "
        "ELSIF NOT EXISTS ("
        "SELECT 1 FROM pg_enum e "
        "JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = 'leadstatus' AND e.enumlabel = 'success'"
        ") THEN "
        "ALTER TYPE leadstatus ADD VALUE 'success'; "
        "END IF; "
        "END $$;"
    )
    op.execute("ALTER TYPE leadstatus ADD VALUE IF NOT EXISTS 'paid'")

    # Create reminders table
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("manager_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("remind_at", sa.DateTime(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("is_sent", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Backfill closed_at for existing success/rejected leads
    op.execute(
        "UPDATE leads SET closed_at = updated_at "
        "WHERE closed_at IS NULL AND status IN ('success', 'rejected')"
    )


def downgrade():
    op.drop_table("reminders")
    op.drop_column("leads", "closed_at")
    op.drop_column("leads", "amount")
    op.drop_column("leads", "email")

    # Best-effort enum rollback (paid value will remain)
    op.execute("ALTER TYPE leadstatus RENAME VALUE 'success' TO 'closed'")
