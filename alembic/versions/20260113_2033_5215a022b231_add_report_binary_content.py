"""add_report_binary_content

Revision ID: 5215a022b231
Revises: 002
Create Date: 2026-01-13 20:33:34.341124

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5215a022b231'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('reports', sa.Column('binary_content', sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column('reports', 'binary_content')
