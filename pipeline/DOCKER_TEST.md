# Docker Build Testing

## Prerequisites

1. Docker Desktop must be running
2. WSL 2 integration enabled (for WSL environments)

## Quick Test

```bash
cd /mnt/c/code/knowledge_extraction/pipeline
./test_docker.sh
```

## Manual Testing

### 1. Build the Image

```bash
docker build -t techfacts-pipeline .
```

Expected output:
- Successfully builds all layers
- Installs Python 3.12
- Installs all requirements
- Copies application code

### 2. Run the Container

```bash
docker run -p 8000:8000 \
  -e API_KEY=test-key-12345 \
  techfacts-pipeline
```

### 3. Test Endpoints

In another terminal:

```bash
# Health check (public)
curl http://localhost:8000/health
# Expected: {"status":"ok", ...}

# Root endpoint (protected - should fail)
curl http://localhost:8000/
# Expected: 401 Unauthorized

# Root endpoint (with API key)
curl -H "X-API-Key: test-key-12345" http://localhost:8000/
# Expected: {"service":"TechFacts Pipeline API", ...}

# Test CORS headers
curl -I -H "Origin: http://localhost:8080" http://localhost:8000/health
# Expected: access-control-allow-origin header present
```

### 4. Check Logs

```bash
docker ps  # Get container ID
docker logs <container-id>
```

### 5. Stop Container

```bash
docker stop <container-id>
docker rm <container-id>
```

## Docker Compose Test

Test with the full stack:

```bash
cd /mnt/c/code/knowledge_extraction
docker compose up pipeline
```

This will start:
- Redis
- PostgreSQL
- Qdrant
- Firecrawl
- Pipeline service

Access at: http://localhost:8000/docs

## Troubleshooting

### Build Fails

Check:
- All files present (requirements.txt, main.py, config.py, middleware/)
- No syntax errors in Python files
- Docker has enough disk space

### Container Won't Start

Check:
- Port 8000 not already in use
- Environment variables set correctly
- Check logs: `docker logs <container-id>`

### Import Errors

Ensure all dependencies in requirements.txt:
- fastapi
- uvicorn
- pydantic-settings
- All middleware dependencies

## Expected Build Time

- First build: ~2-3 minutes (downloads Python, installs deps)
- Subsequent builds: ~10-30 seconds (cached layers)

## Image Size

Expected size: ~200-300MB
- Base python:3.12-slim: ~150MB
- Dependencies: ~50-100MB
- Application code: <1MB
