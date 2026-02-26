"""initial

Revision ID: 0001
Revises:
Create Date: 2025-02-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('managers',
        sa.Column('id',          sa.Integer(),     nullable=False),
        sa.Column('tg_id',       sa.Integer(),     nullable=False),
        sa.Column('name',        sa.String(255),   nullable=False),
        sa.Column('tg_username', sa.String(100),   nullable=True),
        sa.Column('role',        sa.Enum('manager', 'admin', name='managerrole'), nullable=False, server_default='manager'),
        sa.Column('is_active',   sa.Boolean(),     nullable=False, server_default='true'),
        sa.Column('created_at',  sa.DateTime(),    server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tg_id'),
    )
    op.create_table('leads',
        sa.Column('id',            sa.Integer(),   nullable=False),
        sa.Column('name',          sa.String(255), nullable=False),
        sa.Column('phone',         sa.String(50),  nullable=False),
        sa.Column('source',        sa.String(100), nullable=False),
        sa.Column('service',       sa.String(255), nullable=True),
        sa.Column('comment',       sa.Text(),      nullable=False, server_default=''),
        sa.Column('utm_campaign',  sa.String(255), nullable=True),
        sa.Column('utm_source',    sa.String(255), nullable=True),
        sa.Column('extra',         postgresql.JSON(), nullable=True),
        sa.Column('status',        sa.Enum('new','in_progress','closed','rejected', name='leadstatus'), nullable=False, server_default='new'),
        sa.Column('manager_id',    sa.Integer(),   nullable=True),
        sa.Column('reject_reason', sa.Text(),      nullable=True),
        sa.Column('tg_message_id', sa.Integer(),   nullable=True),
        sa.Column('tg_topic_id',   sa.Integer(),   nullable=True),
        sa.Column('created_at',    sa.DateTime(),  server_default=sa.text('now()')),
        sa.Column('updated_at',    sa.DateTime(),  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['manager_id'], ['managers.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('lead_history',
        sa.Column('id',          sa.Integer(),  nullable=False),
        sa.Column('lead_id',     sa.Integer(),  nullable=False),
        sa.Column('from_status', sa.Enum('new','in_progress','closed','rejected', name='leadstatus'), nullable=True),
        sa.Column('to_status',   sa.Enum('new','in_progress','closed','rejected', name='leadstatus'), nullable=False),
        sa.Column('manager_id',  sa.Integer(),  nullable=True),
        sa.Column('comment',     sa.Text(),     nullable=True),
        sa.Column('created_at',  sa.DateTime(), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['lead_id'],    ['leads.id']),
        sa.ForeignKeyConstraint(['manager_id'], ['managers.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('lead_comments',
        sa.Column('id',         sa.Integer(),   nullable=False),
        sa.Column('lead_id',    sa.Integer(),   nullable=False),
        sa.Column('text',       sa.Text(),      nullable=False),
        sa.Column('author',     sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(),  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('lead_comments')
    op.drop_table('lead_history')
    op.drop_table('leads')
    op.drop_table('managers')
