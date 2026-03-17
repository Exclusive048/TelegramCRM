"""fix_group_id_unique: remove unique constraint from tenants.group_id

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-03-05 00:11:00
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade():
    # Drop unique constraint on group_id.
    # Constraint name can be verified with:
    # SELECT constraint_name FROM information_schema.table_constraints
    # WHERE table_name='tenants' AND constraint_type='UNIQUE';
    op.drop_constraint('tenants_group_id_key', 'tenants', type_='unique')
    # Create partial unique index: unique only for group_id != 0.
    op.execute("""
        CREATE UNIQUE INDEX ix_tenants_group_id_nonzero
        ON tenants (group_id)
        WHERE group_id != 0
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_tenants_group_id_nonzero")
    op.create_unique_constraint('tenants_group_id_key', 'tenants', ['group_id'])
