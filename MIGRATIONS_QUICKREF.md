# Alembic Migrations - Quick Reference

## Most Common Commands

```bash
# Commands now run from repo root

# Apply all migrations
./migrate.sh upgrade

# Create new migration after editing orm_models.py
./migrate.sh create "add_something"

# Check current version
./migrate.sh current

# Rollback last migration
./migrate.sh downgrade

# Test migrations
./migrate.sh test
```

## Docker Commands

```bash
# Automatic (on startup)
docker compose up -d

# Manual migration in running container
docker compose exec pipeline python -m alembic upgrade head

# Check migration status
docker compose exec pipeline python -m alembic current

# Migration logs
docker compose logs migrate
```

## Creating Migrations

### 1. Update ORM Model

Edit `src/orm_models.py`:

```python
class MyTable(Base):
    __tablename__ = "my_table"
    # Add new field
    new_field: Mapped[str] = mapped_column(Text, nullable=True)
```

### 2. Generate Migration

```bash
# Commands now run from repo root
./migrate.sh create "add_new_field_to_my_table"
```

### 3. Review Generated File

Check `alembic/versions/YYYYMMDD_HHMM_*_add_new_field_to_my_table.py`

### 4. Test

```bash
./migrate.sh test
```

### 5. Commit

```bash
git add alembic/versions/
git commit -m "Add new_field to my_table"
```

## Troubleshooting

### Database already has tables from init.sql?

```bash
# Option 1: Fresh start (loses data)
docker compose down -v
docker compose up -d

# Option 2: Stamp current version (keeps data)
docker compose exec pipeline python -m alembic stamp head
```

### Migration failed?

```bash
# Check what went wrong
docker compose logs migrate

# Fix and retry
docker compose run --rm migrate
```

### Need to rollback?

```bash
# Rollback one migration
docker compose exec pipeline python -m alembic downgrade -1

# Rollback to specific version
docker compose exec pipeline python -m alembic downgrade 001
```

## Helper Script Commands

```
./migrate.sh upgrade              # Apply all migrations
./migrate.sh downgrade [version]  # Rollback (default: -1)
./migrate.sh current              # Show current version
./migrate.sh history              # Show all migrations
./migrate.sh create <name>        # Create new migration (auto)
./migrate.sh create-manual <name> # Create empty migration
./migrate.sh test                 # Test up/down cycle
./migrate.sh fresh                # Drop and recreate (DANGER!)
./migrate.sh help                 # Show all commands
```

## Full Documentation

See `docs/MIGRATIONS.md` for complete guide.
