#!/bin/bash
# Docker entrypoint script - runs migrations before starting the app

set -e

echo "[INFO] Checking database connection..."
echo "[INFO] DATABASE_URL: ${DATABASE_URL}"

echo "[INFO] Checking current migration version..."
python -m alembic current || echo "[WARN] Could not get current version"

echo "[INFO] Running database migrations..."
python -m alembic upgrade head

echo "[INFO] Verifying migration completed..."
python -m alembic current

echo "[INFO] Starting application..."
exec uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir /app/src
