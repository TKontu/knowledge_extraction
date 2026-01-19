#!/bin/bash
# Docker entrypoint script - runs migrations before starting the app

set -e

echo "[INFO] Checking database connection..."
echo "[INFO] DATABASE_URL: ${DATABASE_URL}"

echo "[INFO] Verifying migration file integrity..."
sha256sum /app/alembic/versions/20260110_001_initial_schema.py || echo "[WARN] Could not hash migration file"
grep -c "updated_at" /app/alembic/versions/20260110_001_initial_schema.py && echo "[INFO] Migration file contains updated_at references" || echo "[ERROR] Migration file missing updated_at!"

echo "[INFO] Checking current migration version..."
python -m alembic current || echo "[WARN] Could not get current version"

echo "[INFO] Running database migrations..."
python -m alembic upgrade head

echo "[INFO] Verifying migration completed..."
python -m alembic current

echo "[INFO] Starting application..."
exec uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir /app/src
