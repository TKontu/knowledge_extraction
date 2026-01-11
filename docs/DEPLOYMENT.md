# Remote Deployment Guide

## Overview

System deployed to `192.168.0.136` via Portainer for remote access within LAN.

## Network Architecture

```
Your Workstation (192.168.0.x)
          ↓ (LAN)
    192.168.0.136 (Host)
          ↓ (Docker Network)
┌─────────────────────────────────────┐
│  Web UI :8080                       │ ← Browse here
│  Pipeline API :8000                 │ ← REST API
│  Grafana :3001 (optional)           │ ← Monitoring
│                                     │
│  Internal Services:                 │
│  - Firecrawl :3002                  │
│  - PostgreSQL :5432                 │
│  - Qdrant :6333                     │
│  - Redis :6379                      │
└─────────────────────────────────────┘
```

## Access Points

| Service | URL | Purpose | Auth |
|---------|-----|---------|------|
| Web UI | `http://192.168.0.136:8080` | Remote control dashboard | API Key |
| API | `http://192.168.0.136:8000` | REST API | API Key |
| Grafana | `http://192.168.0.136:3001` | Metrics (optional) | Built-in |
| Docs | `http://192.168.0.136:8000/docs` | OpenAPI docs | Public |

## Security

### API Key Authentication

All API endpoints (except `/health` and `/docs`) require API key:

```bash
# Set in Portainer environment variables
API_KEY=your-secure-random-key-here

# Use in requests
curl -H "X-API-Key: your-secure-random-key-here" \
  http://192.168.0.136:8000/api/v1/scrape
```

### Network Security

- **Firewall**: Ensure 192.168.0.136 is only accessible within your LAN
- **No HTTPS**: Not needed for internal LAN deployment (can add nginx later if needed)
- **Portainer Access Control**: Use Portainer's auth for stack management

## Remote Control Options

### Option 1: Web UI (Recommended for Remote)

Access `http://192.168.0.136:8080` for:
- Submit scrape jobs
- Monitor job status
- Search extracted facts
- Generate reports
- View logs

### Option 2: REST API

Use curl/scripts for automation:

```bash
# Set API key once
export API_KEY="your-key-here"
export API_BASE="http://192.168.0.136:8000"

# Submit scrape job
curl -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://docs.company.com"], "company": "CompanyName"}' \
  $API_BASE/api/v1/scrape

# Check job status
curl -H "X-API-Key: $API_KEY" \
  $API_BASE/api/v1/jobs/{job_id}
```

### Option 3: Python Client Library (Future)

```python
from scristill import ScristillClient

client = ScristillClient(
    base_url="http://192.168.0.136:8000",
    api_key="your-key-here"
)

job = client.scrape(
    urls=["https://docs.company.com"],
    company="CompanyName"
)
job.wait_complete()
```

## Monitoring & Observability

### Health Checks

```bash
# Check all services healthy
curl http://192.168.0.136:8000/health

# Response:
{
  "status": "healthy",
  "services": {
    "postgres": "up",
    "redis": "up",
    "qdrant": "up",
    "firecrawl": "up"
  }
}
```

### Logs

Via Portainer:
1. Navigate to stack
2. Click service name
3. View "Logs" tab

Or via docker:
```bash
ssh user@192.168.0.136
docker compose -f /path/to/stack/docker-compose.yml logs -f pipeline
```

### Metrics (Optional)

Enable monitoring stack:
```bash
docker compose --profile monitoring up -d
```

Access Grafana at `http://192.168.0.136:3001`:
- Default login: admin/admin
- Pre-configured dashboards for:
  - Job throughput
  - Scrape success rate
  - LLM API latency
  - Storage usage

## Portainer Deployment Steps

### 1. Prepare Environment

In Portainer, create stack environment variables:

```env
# Required
API_KEY=generate-secure-random-key
DB_USER=scristill
DB_PASSWORD=secure-db-password
OPENAI_BASE_URL=http://192.168.0.247:9003/v1
OPENAI_EMBEDDING_BASE_URL=http://192.168.0.136:9003/v1
OPENAI_API_KEY=ollama

# Recommended
LLM_MODEL=gemma3-12b-awq
RAG_EMBEDDING_MODEL=bge-large-en
LOG_LEVEL=INFO

# Optional
ENABLE_METRICS=true
ALLOWED_ORIGINS=http://192.168.0.136:8080
```

### 2. Deploy Stack

1. In Portainer: **Stacks** → **Add Stack**
2. Name: `scristill`
3. Build method: **Git Repository**
   - URL: `https://github.com/TKontu/knowledge_extraction.git`
   - Branch: `main`
   - Compose path: `docker-compose.yml`
4. Add environment variables from step 1
5. Click **Deploy**

### 3. Verify Deployment

```bash
# Check all containers running
curl http://192.168.0.136:8000/health

# Test scrape endpoint (use your API key)
curl -H "X-API-Key: your-key-here" \
  http://192.168.0.136:8000/api/v1/health
```

### 4. Access Web UI

Navigate to: `http://192.168.0.136:8080`

Enter API key when prompted.

## Backup & Restore

### Backup Data

```bash
# PostgreSQL
docker exec scristill-postgres pg_dump -U scristill scristill > backup.sql

# Qdrant vectors
docker exec scristill-qdrant tar czf - /qdrant/storage > qdrant-backup.tar.gz

# Redis (if needed)
docker exec scristill-redis redis-cli --rdb /data/dump.rdb
docker cp scristill-redis:/data/dump.rdb redis-backup.rdb
```

### Restore Data

```bash
# PostgreSQL
cat backup.sql | docker exec -i scristill-postgres psql -U scristill scristill

# Qdrant
docker cp qdrant-backup.tar.gz scristill-qdrant:/tmp/
docker exec scristill-qdrant tar xzf /tmp/qdrant-backup.tar.gz -C /

# Restart services
docker compose restart
```

## Troubleshooting

### Cannot Access Web UI

1. Check container is running: `docker ps | grep webui`
2. Check port mapping: `docker port scristill-webui`
3. Check firewall: `sudo ufw status` (if applicable)
4. Check logs: `docker logs scristill-webui`

### API Returns 401 Unauthorized

- Verify API_KEY environment variable is set
- Check you're passing `X-API-Key` header
- Verify key matches exactly (no extra spaces)

### Services Not Starting

1. Check Portainer stack logs
2. Verify environment variables are set
3. Check vLLM gateway is accessible:
   ```bash
   curl http://192.168.0.247:9003/v1/models
   ```
4. Check disk space: `df -h`

### Slow Scraping

1. Check rate limiting settings (might be too conservative)
2. Check Firecrawl logs for errors
3. Verify network connectivity to target sites
4. Check if FlareSolverr is needed for Cloudflare sites

## Maintenance

### Update Stack

```bash
# In Portainer
1. Go to Stack
2. Click "Editor"
3. Pull latest from Git
4. Click "Update the stack"
```

### Scale Services

For higher load, adjust in docker-compose.yml:

```yaml
pipeline:
  deploy:
    replicas: 2  # Run 2 instances
```

### Clear Old Data

```sql
-- Delete old completed jobs (90+ days)
DELETE FROM jobs
WHERE status = 'completed'
AND completed_at < NOW() - INTERVAL '90 days';

-- Archive old facts to separate table
CREATE TABLE facts_archive AS
SELECT * FROM facts WHERE extracted_at < NOW() - INTERVAL '180 days';

DELETE FROM facts WHERE extracted_at < NOW() - INTERVAL '180 days';
```

## Performance Tuning

### For Lower Memory

```yaml
# Reduce Firecrawl workers
firecrawl-api:
  environment:
    - NUM_WORKERS_PER_QUEUE=1  # Down from 2

# Limit Qdrant memory
qdrant:
  environment:
    - QDRANT__STORAGE__OPTIMIZERS__MAX_MEMORY_KB=524288  # 512MB
```

### For Higher Throughput

```yaml
# Increase concurrent scraping
pipeline:
  environment:
    - SCRAPE_MAX_CONCURRENT_PER_DOMAIN=4  # Up from 2
    - SCRAPE_DELAY_MIN=1  # Down from 2
```

## Security Hardening (Optional)

### Add Nginx Reverse Proxy

```yaml
nginx:
  image: nginx:alpine
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./nginx.conf:/etc/nginx/nginx.conf:ro
    - ./ssl:/etc/nginx/ssl:ro
```

### Enable HTTPS

1. Generate self-signed cert (for LAN):
   ```bash
   openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
     -keyout ssl/key.pem -out ssl/cert.pem
   ```

2. Configure nginx to terminate SSL

### IP Whitelist

Add to nginx.conf:
```nginx
allow 192.168.0.0/24;
deny all;
```
