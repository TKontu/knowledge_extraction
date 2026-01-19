"""Add updated_at field to jobs table

Revision ID: c49062699b64
Revises: 5215a022b231
Create Date: 2026-01-19 08:43:10.720529

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c49062699b64'
down_revision = '5215a022b231'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add updated_at column with default value of NOW()
    op.add_column(
        'jobs',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('NOW()')
        )
    )


def downgrade() -> None:
    # Remove updated_at column
    op.drop_column('jobs', 'updated_at')
