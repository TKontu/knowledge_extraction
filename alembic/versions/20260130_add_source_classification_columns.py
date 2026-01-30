"""add_source_classification_columns

Revision ID: 7b3e4f2a1c8d
Revises: 5a9c2f1d8e3b
Create Date: 2026-01-30 22:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = '7b3e4f2a1c8d'
down_revision = '5a9c2f1d8e3b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add page classification columns to sources table
    op.add_column(
        'sources',
        sa.Column('page_type', sa.String(50), nullable=True)
    )
    op.add_column(
        'sources',
        sa.Column(
            'relevant_field_groups',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True
        )
    )
    op.add_column(
        'sources',
        sa.Column('classification_method', sa.String(20), nullable=True)
    )
    op.add_column(
        'sources',
        sa.Column('classification_confidence', sa.Float(), nullable=True)
    )

    # Index for filtering by page type within a project
    op.create_index(
        'ix_sources_page_type',
        'sources',
        ['project_id', 'page_type']
    )


def downgrade() -> None:
    op.drop_index('ix_sources_page_type', table_name='sources')
    op.drop_column('sources', 'classification_confidence')
    op.drop_column('sources', 'classification_method')
    op.drop_column('sources', 'relevant_field_groups')
    op.drop_column('sources', 'page_type')
