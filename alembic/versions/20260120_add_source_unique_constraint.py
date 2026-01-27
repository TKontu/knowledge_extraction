"""add_source_unique_constraint

Adds unique constraint on (project_id, uri) to sources table.
This prevents duplicate sources from being created when concurrent
crawlers process the same URL.

Revision ID: 8f3a2d1c5e9b
Revises: 5215a022b231
Create Date: 2026-01-20

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "8f3a2d1c5e9b"
down_revision = "5215a022b231"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # First, delete duplicates (keep the earliest one)
    # This SQL keeps the source with the earliest created_at for each (project_id, uri) pair
    op.execute(
        """
        DELETE FROM sources
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY project_id, uri
                           ORDER BY created_at ASC
                       ) as row_num
                FROM sources
            ) ranked
            WHERE row_num > 1
        )
        """
    )

    # Now add the unique constraint
    op.create_unique_constraint(
        "uq_sources_project_uri",
        "sources",
        ["project_id", "uri"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_sources_project_uri", "sources", type_="unique")
