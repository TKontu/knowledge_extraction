#!/usr/bin/env python3
"""Quick script to check database schema."""

import os
from sqlalchemy import create_engine, text

database_url = os.getenv(
    "DATABASE_URL",
    "postgresql://scristill:scristill@localhost:5432/scristill",
)

# Convert to psycopg
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://")

engine = create_engine(database_url)

print(f"Connecting to: {database_url}")
print()

with engine.connect() as conn:
    # Check if jobs table exists
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'jobs'
        );
    """))
    exists = result.scalar()
    print(f"Jobs table exists: {exists}")

    if exists:
        # Get columns
        result = conn.execute(text("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'jobs'
            ORDER BY ordinal_position;
        """))
        print("\nColumns in jobs table:")
        for row in result:
            print(f"  {row[0]:20} {row[1]:20} nullable={row[2]:5} default={row[3]}")

    # Check alembic_version
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'alembic_version'
        );
    """))
    exists = result.scalar()
    print(f"\nAlembic_version table exists: {exists}")

    if exists:
        result = conn.execute(text("SELECT version_num FROM alembic_version;"))
        versions = [row[0] for row in result]
        print(f"Applied migrations: {versions}")
