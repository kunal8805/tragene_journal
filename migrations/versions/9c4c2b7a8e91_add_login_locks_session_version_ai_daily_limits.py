"""add login locks session version ai daily limits

Revision ID: 9c4c2b7a8e91
Revises: 31091ae24e6c
Create Date: 2026-08-19 21:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c4c2b7a8e91'
down_revision = '31091ae24e6c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('failed_attempts', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('locked_until', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('session_version', sa.Integer(), nullable=False, server_default='0'))

    with op.batch_alter_table('ai_plan_defaults', schema=None) as batch_op:
        batch_op.add_column(sa.Column('daily_requests', sa.Integer(), nullable=True))

    op.execute("UPDATE ai_plan_defaults SET daily_requests = 2 WHERE plan_tier = 'free' AND daily_requests IS NULL")
    op.execute("UPDATE ai_plan_defaults SET daily_requests = 50 WHERE plan_tier = 'pro' AND daily_requests IS NULL")
    op.execute("UPDATE ai_plan_defaults SET daily_requests = 150 WHERE plan_tier = 'elite' AND daily_requests IS NULL")

    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.alter_column('failed_attempts', server_default=None)
            batch_op.alter_column('session_version', server_default=None)


def downgrade():
    with op.batch_alter_table('ai_plan_defaults', schema=None) as batch_op:
        batch_op.drop_column('daily_requests')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('session_version')
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_attempts')
