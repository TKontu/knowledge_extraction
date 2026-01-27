"""add_extraction_and_source_columns

Revision ID: 37c9b8ed58b7
Revises: 8f3a2d1c5e9b
Create Date: 2026-01-27 20:30:44.811841

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = '37c9b8ed58b7'
down_revision = '8f3a2d1c5e9b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add entities_extracted flag to extractions table
    op.add_column(
        'extractions',
        sa.Column('entities_extracted', sa.Boolean(), nullable=True, server_default='false')
    )

    # Add created_by_job_id to sources table
    op.add_column(
        'sources',
        sa.Column('created_by_job_id', UUID(), nullable=True)
    )

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_sources_created_by_job_id',
        'sources', 'jobs',
        ['created_by_job_id'], ['id'],
        ondelete='SET NULL'
    )

    # Add index for job_id lookups
    op.create_index(
        'idx_sources_created_by_job_id',
        'sources',
        ['created_by_job_id']
    )


def downgrade() -> None:
    op.drop_index('idx_sources_created_by_job_id', table_name='sources')
    op.drop_constraint('fk_sources_created_by_job_id', 'sources', type_='foreignkey')
    op.drop_column('sources', 'created_by_job_id')
    op.drop_column('extractions', 'entities_extracted')
