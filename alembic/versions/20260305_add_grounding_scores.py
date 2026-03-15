"""add grounding_scores column to extractions

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-05 12:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Idempotent: skip if column already exists
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='extractions' AND column_name='grounding_scores'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "extractions",
            sa.Column(
                "grounding_scores",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )
    # Idempotent: skip if index already exists
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname='ix_extractions_grounding_scores'"
        )
    )
    if not result.fetchone():
        op.create_index(
            "ix_extractions_grounding_scores",
            "extractions",
            ["grounding_scores"],
            postgresql_using="gin",
        )


def downgrade() -> None:
    op.drop_index("ix_extractions_grounding_scores", table_name="extractions")
    op.drop_column("extractions", "grounding_scores")
