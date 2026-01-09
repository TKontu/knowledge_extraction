# Remote Deployment - Architecture Revisions

## Summary

Based on remote deployment to `192.168.0.136` via Portainer, the architecture has been revised to include:

1. **API Key Authentication** - Secure remote access
2. **Web UI** - Browser-based remote control
3. **Enhanced Monitoring** - Health checks and metrics
4. **Network Configuration** - CORS and origin controls

## Key Changes

### 1. Security Layer (NEW)

**API Key Authentication**
- All API endpoints require `X-API-Key` header
- Configured via `API_KEY` environment variable
- Exceptions: `/health` and `/docs` remain public

**Implementation Required:**
```python
# pipeline/api/middleware/auth.py
async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key != settings.API_KEY:
        raise HTTPException(401, "Invalid API key")
```

### 2. Web UI Service (NEW)

**Purpose:** Remote control via browser at `http://192.168.0.136:8080`

**Features:**
- Job submission (scrape, extract, report)
- Job status monitoring with auto-refresh
- Fact search interface
- Activity logs
- API key management

**Stack:**
- Simple HTML/CSS/JavaScript (no framework needed for MVP)
- Nginx container for serving static files
- Communicates with Pipeline API via fetch()

**Directory Structure:**
```
webui/
├── Dockerfile (nginx:alpine)
├── nginx.conf
├── index.html
├── css/
│   └── style.css
└── js/
    ├── api.js
    └── app.js
```

### 3. Enhanced Health Checks (UPDATED)

**Before:** Simple "OK" response
**After:** Detailed dependency status

```json
{
  "status": "healthy",
  "timestamp": "2025-01-09T12:00:00Z",
  "services": {
    "postgres": {"status": "up", "latency_ms": 2},
    "redis": {"status": "up", "latency_ms": 1},
    "qdrant": {"status": "up", "latency_ms": 3},
    "firecrawl": {"status": "up", "latency_ms": 150},
    "vllm_gateway": {"status": "up", "latency_ms": 50}
  }
}
```

### 4. Metrics Endpoint (NEW)

**Purpose:** Prometheus-compatible metrics for monitoring

```
GET /metrics

# HELP jobs_total Total number of jobs by type and status
# TYPE jobs_total counter
jobs_total{type="scrape",status="completed"} 42
jobs_total{type="scrape",status="failed"} 3
jobs_total{type="extract",status="completed"} 38

# HELP llm_request_duration_seconds LLM request duration
# TYPE llm_request_duration_seconds histogram
llm_request_duration_seconds_bucket{le="1.0"} 10
llm_request_duration_seconds_bucket{le="5.0"} 45
```

### 5. CORS Configuration (NEW)

**Purpose:** Allow Web UI to call API from different port

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Updated Environment Variables

### Required (New)
```bash
API_KEY=your-secure-random-key  # Generate with: openssl rand -hex 32
```

### Optional (New)
```bash
ALLOWED_ORIGINS=http://192.168.0.136:8080,http://localhost:8080
LOG_FORMAT=json  # or 'pretty' for local dev
ENABLE_METRICS=true
```

## Docker Compose Changes

### Added Service
```yaml
webui:
  build: ./webui
  ports:
    - "8080:80"
  environment:
    - PIPELINE_API_URL=http://pipeline:8000
    - API_KEY=${API_KEY}
```

### Updated Pipeline Service
```yaml
pipeline:
  environment:
    # Added:
    - API_KEY=${API_KEY}
    - ALLOWED_ORIGINS=${ALLOWED_ORIGINS}
    - LOG_FORMAT=${LOG_FORMAT}
    - ENABLE_METRICS=${ENABLE_METRICS}
```

## Network Access Summary

| Port | Service | Access | Auth Required |
|------|---------|--------|---------------|
| 8080 | Web UI | Public (LAN) | API Key in UI |
| 8000 | Pipeline API | Public (LAN) | X-API-Key header |
| 3002 | Firecrawl | Internal only | N/A |
| 6379 | Redis | Internal only | N/A |
| 6333 | Qdrant | Internal only | N/A |
| 5432 | PostgreSQL | Internal only | N/A |

## Implementation Priority

### Phase 1.1 (Security - CRITICAL)
1. API key authentication middleware
2. CORS configuration
3. Update health check endpoint

### Phase 1.2 (Remote Control - HIGH)
1. Create Web UI directory structure
2. Build simple HTML/JS dashboard
3. Add job submission forms
4. Add job status display
5. Containerize with nginx

### Phase 1.3 (Observability - MEDIUM)
1. Add Prometheus metrics
2. Enhance logging with structured format
3. Add request tracing

## Testing Checklist

### Security
- [ ] API returns 401 without API key
- [ ] API accepts requests with valid API key
- [ ] CORS allows Web UI origin
- [ ] CORS blocks unauthorized origins

### Web UI
- [ ] Can access UI at http://192.168.0.136:8080
- [ ] Can submit scrape job
- [ ] Can view job status
- [ ] Auto-refresh works
- [ ] Error messages display properly

### Remote Access
- [ ] Can access from different machine on LAN
- [ ] Cannot access from outside LAN (firewall test)
- [ ] Health check works without auth
- [ ] Docs (/docs) work without auth

## Migration from Original Plan

### What Stayed the Same
- Core services (Firecrawl, PostgreSQL, Qdrant, Redis)
- Data models and schemas
- LLM integration approach
- Module structure (scraper, extraction, storage, reports)

### What Changed
- **Added:** API authentication (was missing)
- **Added:** Web UI service (was "future enhancement", now required)
- **Added:** Metrics endpoint (was "basic metrics", now Prometheus format)
- **Enhanced:** Health checks (from simple to detailed)
- **Enhanced:** Logging (added structured format option)

### Why These Changes
1. **Remote deployment** requires secure API access
2. **Portainer access** is for infrastructure only, not operations
3. **Browser-based control** is easier than SSH + curl for operations
4. **Observability** is critical when services run remotely

## Next Steps

1. **Complete infrastructure revisions** (Phase 1.1-1.3)
2. **Test locally** with docker-compose
3. **Deploy to Portainer** on 192.168.0.136
4. **Verify remote access** from workstation
5. **Proceed with core development** (Phases 2-7)

## Documentation Updates

Created:
- ✅ `docs/DEPLOYMENT.md` - Remote deployment guide
- ✅ `docs/REMOTE_DEPLOYMENT_REVISIONS.md` - This document

Updated:
- ✅ `docs/TODO.md` - Added security and Web UI tasks
- ✅ `docker-compose.yml` - Added webui service and security env vars
- ✅ `.env.example` - Added API_KEY and monitoring variables

## Reference

For complete deployment instructions, see: [`docs/DEPLOYMENT.md`](./DEPLOYMENT.md)
