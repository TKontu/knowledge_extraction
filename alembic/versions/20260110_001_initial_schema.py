"""initial_schema

Revision ID: 001
Revises:
Create Date: 2026-01-10 22:10:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all initial tables."""
    # Enable UUID extension
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ===================
    # GENERALIZED SCHEMA
    # ===================

    # Projects Table
    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "source_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text('\'{"type": "web", "group_by": "company"}\''),
        ),
        sa.Column("extraction_schema", postgresql.JSONB(), nullable=False),
        sa.Column(
            "entity_types",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "prompt_templates",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "is_template", sa.Boolean(), default=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_active", sa.Boolean(), default=True, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
    )
    op.create_index("idx_projects_name", "projects", ["name"])
    op.create_index("idx_projects_active", "projects", ["is_active"])

    # Sources Table
    op.create_table(
        "sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_type", sa.Text(), nullable=False, server_default=sa.text("'web'")
        ),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("source_group", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'")),
        sa.Column("outbound_links", postgresql.JSONB(), server_default=sa.text("'[]'")),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "uri"),
    )
    op.create_index("idx_sources_project", "sources", ["project_id"])
    op.create_index("idx_sources_group", "sources", ["source_group"])
    op.create_index("idx_sources_status", "sources", ["status"])

    # Extractions Table
    op.create_table(
        "extractions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("extraction_type", sa.Text(), nullable=False),
        sa.Column("source_group", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("profile_used", sa.Text(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("chunk_context", postgresql.JSONB(), nullable=True),
        sa.Column("embedding_id", sa.Text(), nullable=True),
        sa.Column(
            "extracted_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_extractions_project", "extractions", ["project_id"])
    op.create_index("idx_extractions_source", "extractions", ["source_id"])
    op.create_index("idx_extractions_group", "extractions", ["source_group"])
    op.create_index("idx_extractions_type", "extractions", ["extraction_type"])
    op.create_index("idx_extractions_confidence", "extractions", ["confidence"])
    op.create_index(
        "idx_extractions_data", "extractions", ["data"], postgresql_using="gin"
    )

    # Entities Table
    op.create_table(
        "entities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_group", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "project_id", "source_group", "entity_type", "normalized_value"
        ),
    )
    op.create_index("idx_entities_project", "entities", ["project_id"])
    op.create_index("idx_entities_group", "entities", ["source_group"])
    op.create_index("idx_entities_type", "entities", ["entity_type"])

    # Extraction-Entity Junction Table
    op.create_table(
        "extraction_entities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'mention'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["extractions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("extraction_id", "entity_id", "role"),
    )

    # ===================
    # LEGACY SCHEMA
    # ===================

    # Pages Table
    op.create_table(
        "pages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("url", sa.Text(), unique=True, nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("markdown_content", sa.Text(), nullable=True),
        sa.Column(
            "scraped_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'completed'")),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
    )
    op.create_index("idx_pages_company", "pages", ["company"])
    op.create_index("idx_pages_domain", "pages", ["domain"])
    op.create_index("idx_pages_status", "pages", ["status"])
    op.create_index("idx_pages_scraped_at", "pages", ["scraped_at"])

    # Facts Table
    op.create_table(
        "facts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_text", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("profile_used", sa.Text(), nullable=False),
        sa.Column("embedding_id", sa.Text(), nullable=True),
        sa.Column(
            "extracted_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_facts_page_id", "facts", ["page_id"])
    op.create_index("idx_facts_category", "facts", ["category"])
    op.create_index("idx_facts_profile", "facts", ["profile_used"])
    op.create_index("idx_facts_confidence", "facts", ["confidence"])

    # Jobs Table
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'")),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0")),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_jobs_status", "jobs", ["status"])
    op.create_index("idx_jobs_type", "jobs", ["type"])
    op.create_index("idx_jobs_project", "jobs", ["project_id"])
    op.create_index("idx_jobs_created_at", "jobs", ["created_at"])
    op.create_index("idx_jobs_updated_at", "jobs", ["updated_at"])

    # Profiles Table
    op.create_table(
        "profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("name", sa.Text(), unique=True, nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("prompt_focus", sa.Text(), nullable=False),
        sa.Column("depth", sa.Text(), nullable=False),
        sa.Column("custom_instructions", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
    )

    # Reports Table
    op.create_table(
        "reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("source_groups", postgresql.JSONB(), server_default=sa.text("'[]'")),
        sa.Column("categories", postgresql.JSONB(), server_default=sa.text("'[]'")),
        sa.Column("extraction_ids", postgresql.JSONB(), server_default=sa.text("'[]'")),
        sa.Column("format", sa.Text(), server_default=sa.text("'md'")),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_reports_type", "reports", ["type"])
    op.create_index("idx_reports_created_at", "reports", ["created_at"])

    # Rate Limits Table
    op.create_table(
        "rate_limits",
        sa.Column("domain", sa.Text(), primary_key=True),
        sa.Column("request_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("last_request", sa.DateTime(timezone=True), nullable=True),
        sa.Column("daily_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("daily_reset_at", sa.Date(), server_default=sa.text("CURRENT_DATE")),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("rate_limits")
    op.drop_table("reports")
    op.drop_table("profiles")
    op.drop_table("jobs")
    op.drop_table("facts")
    op.drop_table("pages")
    op.drop_table("extraction_entities")
    op.drop_table("entities")
    op.drop_table("extractions")
    op.drop_table("sources")
    op.drop_table("projects")
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
