# TODO: Database Migrations (Alembic)

## Overview

Replace `init.sql` with Alembic migrations for safe schema evolution.

## Status

✅ **IMPLEMENTATION COMPLETE** - 2026-01-10

**Implementation Summary:**
- ✅ SQLAlchemy ORM models reconciled with init.sql
- ✅ Alembic fully configured and integrated
- ✅ Initial migration created (001_initial_schema.py)
- ✅ Seed migration for builtin profiles (002_seed_builtin_profiles.py)
- ✅ Docker-compose integration with automatic migrations
- ✅ Comprehensive documentation and helper scripts
- ✅ Migration system ready for production use

**See:** `ALEMBIC_SETUP_COMPLETE.md` for full implementation summary

**Original Problem (SOLVED):**
- ✅ Schema changes now version controlled
- ✅ Upgrade path for existing databases
- ✅ Automatic migrations on deployment
- ✅ Safe rollback capability

---

## Implementation Tasks

### 1. Install Dependencies

```bash
# Add to requirements.txt
alembic>=1.13.0
```

### 2. Reconcile ORM Models with init.sql

Before generating migrations, verify ORM models match intended schema:

- [ ] Compare `orm_models.py` with `init.sql`
- [ ] Add any missing columns to ORM (e.g., `attempt_count`, `max_attempts` on jobs)
- [ ] Verify column types match
- [ ] Verify indexes match

### 3. Project Structure

```
pipeline/
├── alembic/
│   ├── versions/           # Migration files
│   │   ├── 001_initial_schema.py
│   │   └── 002_seed_builtin_profiles.py
│   ├── env.py              # Alembic environment config
│   └── script.py.mako      # Migration template
├── alembic.ini             # Alembic config
└── orm_models.py           # SQLAlchemy models (source of truth)
```

### 4. Create alembic.ini

```ini
[alembic]
script_location = alembic
prepend_sys_path = .

[post_write_hooks]
hooks = ruff
ruff.type = console_scripts
ruff.entrypoint = ruff
ruff.options = format
```

### 5. Create alembic/env.py (Async-Compatible)

```python
import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
import os

from orm_models import Base

config = context.config

# Set URL from environment
database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://techfacts:techfacts@localhost:5432/techfacts")
config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### 6. Generate Initial Migration

```bash
cd pipeline

# Initialize alembic (creates alembic/ directory)
alembic init alembic

# Replace generated env.py with async version above

# Generate migration from ORM models
alembic revision --autogenerate -m "initial_schema"
```

### 7. Create Seed Migration for Builtin Profiles

```python
"""seed_builtin_profiles

Revision ID: 002
Revises: 001
"""
from alembic import op
import sqlalchemy as sa
from uuid import uuid4
from datetime import datetime, timezone

revision = '002'
down_revision = '001'

BUILTIN_PROFILES = [
    {
        "name": "technical_specs",
        "categories": ["specs", "hardware", "requirements", "compatibility", "performance"],
        "prompt_focus": "Hardware specifications, system requirements, supported platforms, performance metrics",
        "depth": "detailed",
    },
    {
        "name": "api_docs",
        "categories": ["endpoints", "authentication", "rate_limits", "sdks", "versioning"],
        "prompt_focus": "API endpoints, authentication methods, rate limits, SDK availability",
        "depth": "detailed",
    },
    {
        "name": "security",
        "categories": ["certifications", "compliance", "encryption", "audit", "access_control"],
        "prompt_focus": "Security certifications, compliance standards, encryption methods",
        "depth": "comprehensive",
    },
    {
        "name": "pricing",
        "categories": ["pricing", "tiers", "limits", "features"],
        "prompt_focus": "Pricing tiers, feature inclusions, usage limits",
        "depth": "detailed",
    },
    {
        "name": "general",
        "categories": ["general", "features", "technical", "integration"],
        "prompt_focus": "General technical facts, features, integrations",
        "depth": "summary",
    },
]


def upgrade() -> None:
    profiles_table = sa.table(
        'profiles',
        sa.column('id', sa.String),
        sa.column('name', sa.String),
        sa.column('categories', sa.ARRAY(sa.String)),
        sa.column('prompt_focus', sa.String),
        sa.column('depth', sa.String),
        sa.column('is_builtin', sa.Boolean),
        sa.column('created_at', sa.DateTime),
    )

    op.bulk_insert(profiles_table, [
        {
            "id": str(uuid4()),
            "name": p["name"],
            "categories": p["categories"],
            "prompt_focus": p["prompt_focus"],
            "depth": p["depth"],
            "is_builtin": True,
            "created_at": datetime.now(timezone.utc),
        }
        for p in BUILTIN_PROFILES
    ])


def downgrade() -> None:
    op.execute("DELETE FROM profiles WHERE is_builtin = true")
```

### 8. Docker Integration

**Option A: Run migrations before app starts**
```yaml
# docker-compose.yml
services:
  pipeline:
    command: >
      sh -c "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"
```

**Option B: Separate init container (safer)**
```yaml
services:
  migrate:
    build: ./pipeline
    command: alembic upgrade head
    environment:
      DATABASE_URL: ${DATABASE_URL}
    depends_on:
      postgres:
        condition: service_healthy

  pipeline:
    depends_on:
      migrate:
        condition: service_completed_successfully
```

### 9. Remove init.sql

After migrations are working:
- [ ] Delete `init.sql`
- [ ] Remove init.sql volume mount from docker-compose
- [ ] Update README with migration commands

---

## Commands Reference

```bash
# Apply all pending migrations
alembic upgrade head

# Apply specific migration
alembic upgrade 002

# Rollback one migration
alembic downgrade -1

# Rollback to specific version
alembic downgrade 001

# Show current version
alembic current

# Show migration history
alembic history

# Generate new migration from model changes
alembic revision --autogenerate -m "description"
```

---

## Migration Workflow

### Adding a New Column

```bash
# 1. Update SQLAlchemy model in orm_models.py
# 2. Generate migration
alembic revision --autogenerate -m "add_column_x_to_facts"

# 3. Review generated migration (alembic may miss some things)
# 4. Test locally
alembic upgrade head

# 5. Commit migration file
git add alembic/versions/
git commit -m "Add column x to facts table"
```

### Production Deployment

```bash
# 1. Deploy new code (includes migration files)
# 2. Run migrations before starting app
docker compose exec pipeline alembic upgrade head

# 3. Or: use init container (automatic)
```

---

## Tasks

- [ ] Add alembic to requirements.txt
- [ ] Reconcile ORM models with init.sql (verify match)
- [ ] Create `alembic.ini`
- [ ] Create `alembic/env.py` (async-compatible)
- [ ] Generate initial migration from ORM models
- [ ] Create seed migration for builtin profiles
- [ ] Test upgrade and downgrade paths
- [ ] Update docker-compose for migrations
- [ ] Remove init.sql
- [ ] Document migration workflow in README

---

## Testing Checklist

- [ ] Fresh database: `alembic upgrade head` creates all tables
- [ ] Downgrade: `alembic downgrade base` removes all tables
- [ ] Upgrade/downgrade cycle: no data loss
- [ ] Autogenerate: detects model changes correctly
- [ ] Seed data: builtin profiles exist after migration
- [ ] Docker: migrations run before app starts
