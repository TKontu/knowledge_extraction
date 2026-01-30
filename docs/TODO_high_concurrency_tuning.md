# TODO: High Concurrency Tuning

## Overview

This document outlines configuration improvements for high-throughput crawling scenarios (e.g., 300+ domains, 50+ pages each).

## Current Bottlenecks Identified

| Component | Current Setting | Bottleneck Impact |
|-----------|-----------------|-------------------|
| Camoufox browser pool | 5 browsers | PRIMARY - limits concurrent page loads |
| Firecrawl workers | 6 workers | SECONDARY - waits for browsers |
| Pipeline crawl workers | 6 workers | Fine - just polls status |
| DB connection pool | 5+10 connections | Fine for current scale |

## Recommended Configuration Changes

### 1. Camoufox Browser Pool (HIGH Priority)

**Current:**
```yaml
CAMOUFOX_BROWSER_COUNT=5
CAMOUFOX_POOL_SIZE=10
```

**Recommended for high throughput:**
```yaml
CAMOUFOX_BROWSER_COUNT=20
CAMOUFOX_POOL_SIZE=40
```

**Memory impact:** ~1GB per browser, so 20 browsers ≈ 20GB. Current 24GB limit is sufficient.

**Implementation:** Update `docker-compose.prod.yml` and `.env.example`

### 2. Firecrawl Workers (HIGH Priority)

**Current:**
```yaml
NUM_WORKERS_PER_QUEUE=6
```

**Recommended:** Match to Camoufox browser count
```yaml
NUM_WORKERS_PER_QUEUE=20
```

**Rationale:** Firecrawl workers wait for Camoufox browsers. Having more workers than browsers wastes memory; having fewer underutilizes browser capacity.

**Implementation:** Update `docker-compose.prod.yml` and `.env.example`

### 3. Pipeline Crawl Workers (MEDIUM Priority)

**Current:**
```yaml
MAX_CONCURRENT_CRAWLS=6
```

**Recommended for 300+ domain scenarios:**
```yaml
MAX_CONCURRENT_CRAWLS=12
```

**Rationale:** More concurrent crawl jobs means better domain parallelism. Each crawl job is lightweight (just polls Firecrawl status).

**Implementation:** Already configurable via environment variable.

### 4. Database Connection Pool (LOW Priority)

**Current:**
```yaml
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
```

**Recommended if scaling beyond 12 crawl workers:**
```yaml
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
```

**Rationale:** Each worker needs a DB connection. Current 15 connections supports ~12 concurrent workers safely.

### 5. Container Resource Limits (MEDIUM Priority)

Consider creating tiered configurations:

**Small server (16GB RAM, 4 cores):**
```yaml
CAMOUFOX_BROWSER_COUNT=5
NUM_WORKERS_PER_QUEUE=6
MAX_CONCURRENT_CRAWLS=3
```

**Medium server (48GB RAM, 8 cores):**
```yaml
CAMOUFOX_BROWSER_COUNT=15
NUM_WORKERS_PER_QUEUE=15
MAX_CONCURRENT_CRAWLS=6
```

**Large server (128GB RAM, 16+ cores):**
```yaml
CAMOUFOX_BROWSER_COUNT=30
NUM_WORKERS_PER_QUEUE=30
MAX_CONCURRENT_CRAWLS=12
```

## Capacity Estimation Formula

```
Estimated time = (total_pages / concurrent_browsers) × avg_page_time

Example: 300 domains × 50 pages = 15,000 pages
- 5 browsers, 5s/page: 15000/5 × 5s = 4.2 hours
- 20 browsers, 5s/page: 15000/20 × 5s = 1 hour
```

## Implementation Checklist

- [ ] Update `.env.example` with recommended high-throughput values as comments
- [ ] Add `CAMOUFOX_BROWSER_COUNT` guidance in docker-compose comments
- [ ] Document memory requirements per browser count
- [ ] Consider adding a `--profile high-throughput` docker-compose override
- [ ] Add monitoring/metrics for browser pool utilization
- [ ] Test with 100+ domain crawl to validate improvements

## Related Issues

- Polling interval fix (implemented separately) - was causing 30-minute delays
- See `config.py` for `CRAWL_POLL_INTERVAL` setting
