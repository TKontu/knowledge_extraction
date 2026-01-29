"""add_job_cancellation_requested_at

Revision ID: 5a9c2f1d8e3b
Revises: 37c9b8ed58b7
Create Date: 2026-01-28 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5a9c2f1d8e3b'
down_revision = '37c9b8ed58b7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add cancellation_requested_at field to jobs table
    op.add_column(
        'jobs',
        sa.Column('cancellation_requested_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('jobs', 'cancellation_requested_at')
