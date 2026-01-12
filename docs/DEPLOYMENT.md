# Deployment Guide

## Prerequisites

- Docker & Docker Compose v2.x
- 8GB RAM minimum (16GB recommended)
- Access to vLLM gateway (default: 192.168.0.247:9003)
- Access to embedding service (default: 192.168.0.136:9003)

## Quick Start

1. Clone the repository
2. Copy and configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your settings - especially API_KEY!
   ```

3. Deploy:
   ```bash
   docker compose up -d
   ```

4. Verify:
   ```bash
   curl http://localhost:8000/health
   ```

## Environment Variables

### Required
- `API_KEY` - Authentication key (no default - must be set)
- `DATABASE_URL` - PostgreSQL connection string

### Optional (with defaults)
- `REDIS_URL` - Redis connection (default: redis://redis:6379)
- `QDRANT_URL` - Qdrant URL (default: http://qdrant:6333)
- See `.env.example` for full list

## Migrations

Migrations run automatically on startup via the `migrate` service.

To run manually:
```bash
docker compose exec pipeline alembic upgrade head
```

## Scaling

The pipeline service can be scaled horizontally:
```bash
docker compose up -d --scale pipeline=3
```

Note: Requires load balancer in front.

## Monitoring

- Health check: `GET /health`
- Prometheus metrics: `GET /metrics`

## Backup

PostgreSQL backup:
```bash
docker compose exec postgres pg_dump -U scristill scristill > backup.sql
```

Restore:
```bash
cat backup.sql | docker compose exec -T postgres psql -U scristill scristill
```

## Troubleshooting

### Migration failures
Check migrate service logs:
```bash
docker compose logs migrate
```

### Health check failures
```bash
curl -s http://localhost:8000/health | jq
```

### Memory issues
Increase limits in docker-compose.yml deploy section.
