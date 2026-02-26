"""add_domain_boilerplate

Revision ID: a1b2c3d4e5f6
Revises: 7b3e4f2a1c8d
Create Date: 2026-02-26 12:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "7b3e4f2a1c8d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New table: domain_boilerplate
    op.create_table(
        "domain_boilerplate",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column(
            "boilerplate_hashes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("pages_analyzed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocks_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "blocks_boilerplate", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "bytes_removed_avg", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "threshold_pct",
            sa.Float(),
            nullable=False,
            server_default="0.7",
        ),
        sa.Column("min_pages", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("min_block_chars", sa.Integer(), nullable=False, server_default="50"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id", "domain", name="uq_domain_boilerplate_project_domain"
        ),
    )
    op.create_index("ix_domain_boilerplate_domain", "domain_boilerplate", ["domain"])
    op.create_index(
        "ix_domain_boilerplate_project_domain",
        "domain_boilerplate",
        ["project_id", "domain"],
    )

    # New column on sources: cleaned_content
    op.add_column("sources", sa.Column("cleaned_content", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "cleaned_content")
    op.drop_index(
        "ix_domain_boilerplate_project_domain", table_name="domain_boilerplate"
    )
    op.drop_index("ix_domain_boilerplate_domain", table_name="domain_boilerplate")
    op.drop_table("domain_boilerplate")
