#!/bin/bash
# Docker entrypoint script - runs migrations before starting the app

set -e

echo "[INFO] Running database migrations..."
python -m alembic upgrade head

echo "[INFO] Starting application..."
exec uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir /app/src
