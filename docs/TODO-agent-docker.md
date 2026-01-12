# TODO: Docker Cleanup & Hardening

**Agent ID**: `agent-docker`
**Branch**: `feat/docker-hardening`
**Priority**: 1

## Objective

Remove legacy init.sql, add container resource limits, and create deployment documentation.

## Context

- Alembic migrations are working (`migrate` service in docker-compose.yml)
- `init.sql` still exists but is no longer mounted (commented out)
- No resource limits on containers (memory, CPU)
- No deployment documentation exists

## Tasks

### 1. Remove init.sql

**File**: `init.sql` (delete)

Delete the file - it's replaced by Alembic migrations.

```bash
rm init.sql
```

### 2. Add resource limits to docker-compose.yml

**File**: `docker-compose.yml`

Add deploy section with resource limits to each service:

```yaml
services:
  pipeline:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '2'
        reservations:
          memory: 512M
          cpus: '0.5'

  postgres:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '1'
        reservations:
          memory: 256M

  redis:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'
        reservations:
          memory: 128M

  qdrant:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '1'
        reservations:
          memory: 512M

  firecrawl-api:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '1'
        reservations:
          memory: 256M

  playwright:
    # ... existing config ...
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '1'
        reservations:
          memory: 256M
```

### 3. Add health check logging

**File**: `src/main.py`

Update the health endpoint to log when dependencies are unhealthy:

```python
# In health_check function, add logging for failures
if not db_healthy:
    logger.warning("health_check_failed", component="database")
if not redis_healthy:
    logger.warning("health_check_failed", component="redis")
# etc.
```

### 4. Create deployment documentation

**File**: `docs/DEPLOYMENT.md` (new file)

Create deployment guide:

```markdown
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
```

### 5. Write tests

**File**: `tests/test_deployment.py`

```python
"""Tests for deployment configuration."""

import pytest
import yaml
from pathlib import Path


class TestDockerCompose:
    @pytest.fixture
    def compose_config(self) -> dict:
        """Load docker-compose.yml."""
        compose_path = Path(__file__).parent.parent / "docker-compose.yml"
        with open(compose_path) as f:
            return yaml.safe_load(f)

    def test_all_services_have_resource_limits(self, compose_config):
        """All services should have resource limits defined."""
        services = compose_config.get("services", {})

        # Services that must have limits
        required_services = ["pipeline", "postgres", "redis", "qdrant"]

        for service_name in required_services:
            service = services.get(service_name, {})
            deploy = service.get("deploy", {})
            resources = deploy.get("resources", {})
            limits = resources.get("limits", {})

            assert "memory" in limits, f"{service_name} missing memory limit"

    def test_migrate_service_exists(self, compose_config):
        """Migration service should be configured."""
        services = compose_config.get("services", {})
        assert "migrate" in services

        migrate = services["migrate"]
        assert "alembic" in migrate.get("command", "")

    def test_pipeline_depends_on_migrate(self, compose_config):
        """Pipeline should wait for migrations."""
        services = compose_config.get("services", {})
        pipeline = services.get("pipeline", {})
        depends = pipeline.get("depends_on", {})

        assert "migrate" in depends


class TestDeploymentDocs:
    def test_deployment_docs_exist(self):
        """Deployment documentation should exist."""
        docs_path = Path(__file__).parent.parent / "docs" / "DEPLOYMENT.md"
        assert docs_path.exists(), "docs/DEPLOYMENT.md should exist"

    def test_deployment_docs_has_required_sections(self):
        """Deployment docs should have key sections."""
        docs_path = Path(__file__).parent.parent / "docs" / "DEPLOYMENT.md"
        content = docs_path.read_text()

        required_sections = [
            "Prerequisites",
            "Quick Start",
            "Environment Variables",
            "Migrations",
            "Backup",
        ]

        for section in required_sections:
            assert section in content, f"Missing section: {section}"
```

## Constraints

- Do NOT change migration logic (it's working)
- Do NOT modify service ports
- Resource limits should be reasonable for 16GB host machine
- Keep backward compatibility with existing deployments
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_deployment.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/main.py tests/test_deployment.py
```

## Verification

Before creating PR, run ONLY the scoped commands above:

1. `pytest tests/test_deployment.py -v` - Must pass
2. `ruff check src/main.py tests/test_deployment.py` - Must be clean
3. `init.sql` deleted
4. `docs/DEPLOYMENT.md` created
5. All services have resource limits in docker-compose.yml

## Definition of Done

- [ ] `init.sql` deleted
- [ ] Resource limits added to all services in docker-compose.yml
- [ ] Health check logging added to main.py
- [ ] `docs/DEPLOYMENT.md` created with deployment guide
- [ ] Tests written and passing
- [ ] PR created with title: `feat: docker hardening and deployment docs`
