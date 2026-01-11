"""seed_builtin_profiles

Revision ID: 002
Revises: 001
Create Date: 2026-01-10 22:15:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
from uuid import uuid4

# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

# Built-in extraction profiles
BUILTIN_PROFILES = [
    {
        "name": "technical_specs",
        "categories": ["specs", "hardware", "requirements", "compatibility", "performance"],
        "prompt_focus": "Hardware specifications, system requirements, supported platforms, performance metrics, compatibility information",
        "depth": "detailed",
    },
    {
        "name": "api_docs",
        "categories": ["endpoints", "authentication", "rate_limits", "sdks", "versioning"],
        "prompt_focus": "API endpoints, authentication methods, rate limits, SDK availability, API versioning, request/response formats",
        "depth": "detailed",
    },
    {
        "name": "security",
        "categories": ["certifications", "compliance", "encryption", "audit", "access_control"],
        "prompt_focus": "Security certifications (SOC2, ISO27001, etc), compliance standards, encryption methods, audit capabilities, access control features",
        "depth": "comprehensive",
    },
    {
        "name": "pricing",
        "categories": ["pricing", "tiers", "limits", "features"],
        "prompt_focus": "Pricing tiers, feature inclusions per tier, usage limits, enterprise options, free tier details",
        "depth": "detailed",
    },
    {
        "name": "general",
        "categories": ["general", "features", "technical", "integration"],
        "prompt_focus": "General technical facts about the product, features, integrations, and capabilities",
        "depth": "summary",
    },
]


def upgrade() -> None:
    """Insert built-in extraction profiles."""
    # Create profiles table reference for bulk insert
    profiles_table = sa.table(
        "profiles",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("categories", sa.ARRAY(sa.String)),
        sa.column("prompt_focus", sa.String),
        sa.column("depth", sa.String),
        sa.column("is_builtin", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    # Prepare insert data
    now = datetime.now(timezone.utc)
    insert_data = [
        {
            "id": uuid4(),
            "name": p["name"],
            "categories": p["categories"],
            "prompt_focus": p["prompt_focus"],
            "depth": p["depth"],
            "is_builtin": True,
            "created_at": now,
            "updated_at": now,
        }
        for p in BUILTIN_PROFILES
    ]

    # Bulk insert profiles
    op.bulk_insert(profiles_table, insert_data)


def downgrade() -> None:
    """Remove built-in extraction profiles."""
    op.execute("DELETE FROM profiles WHERE is_builtin = true")
