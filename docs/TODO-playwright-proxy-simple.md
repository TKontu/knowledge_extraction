# Playwright Proxy Configuration - Simple Implementation

**Status:** ✅ SIMPLIFIED - No fork required!
**Discovery:** Playwright service already supports proxy via environment variables
**Effort:** 1-2 hours (was 1-2 weeks)

---

## Executive Summary

The Firecrawl Playwright service **already has proxy support**. We just need to pass the `PROXY_SERVER` environment variable pointing to our proxy-adapter.

### What We Found

In `apps/playwright-service-ts/api.ts` (lines 18-123):

```typescript
const PROXY_SERVER = process.env.PROXY_SERVER || null;
const PROXY_USERNAME = process.env.PROXY_USERNAME || null;
const PROXY_PASSWORD = process.env.PROXY_PASSWORD || null;

const createContext = async (skipTlsVerification: boolean = false) => {
  // ...
  if (PROXY_SERVER && PROXY_USERNAME && PROXY_PASSWORD) {
    contextOptions.proxy = {
      server: PROXY_SERVER,
      username: PROXY_USERNAME,
      password: PROXY_PASSWORD,
    };
  } else if (PROXY_SERVER) {
    contextOptions.proxy = {
      server: PROXY_SERVER,  // ← Already implemented!
    };
  }
  // ...
}
```

**This means:** We can configure Playwright's proxy by simply setting an environment variable!

---

## Implementation Tasks

### Task 1: Update Docker Compose Configuration

**File:** `docker-compose.yml`

**Change:**
```yaml
playwright:
  build:
    context: .
    dockerfile: Dockerfile.playwright
  environment:
    - PROXY_SERVER=http://proxy-adapter:8192  # ← ADD THIS
  cap_add:
    - NET_ADMIN
  depends_on:
    - proxy-adapter
  networks:
    - scristill
```

**File:** `docker-compose.prod.yml`

**Change:** Same as above

**Why:** Playwright service will read `PROXY_SERVER` and configure browser contexts to use it.

---

### Task 2: Remove iptables Transparent Proxy (Optional)

Since we're now using explicit proxy configuration, the iptables approach is no longer needed.

**Options:**

**Option A: Keep It (Recommended)**
- Leave iptables as fallback/defense-in-depth
- No harm in having both
- Already implemented and working

**Option B: Remove It**
- Revert to standard Playwright image
- Remove custom entrypoint script
- Simplify deployment

**Recommendation:** Keep it for now, remove later if proven unnecessary.

---

### Task 3: Test Full Crawl Flow

**Test Case 1: WEG (Akamai-Protected)**
```bash
curl -X POST http://localhost:8000/api/v1/crawl \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://www.weg.net",
    "project_id": "<PROJECT_ID>",
    "company": "WEG-Test",
    "max_depth": 2,
    "limit": 10
  }'
```

**Expected Result:**
- ✅ 10 pages crawled (not just 1)
- ✅ Link discovery working
- ✅ FlareSolverr bypassing Akamai
- ✅ Full HTML content

**Test Case 2: Brevini (Non-Blocked)**
```bash
curl -X POST http://localhost:8000/api/v1/crawl \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://www.brevini.com",
    "project_id": "<PROJECT_ID>",
    "company": "Brevini-Test",
    "max_depth": 2,
    "limit": 10
  }'
```

**Expected Result:**
- ✅ 10 pages crawled
- ✅ No proxy overhead (direct connection)

---

### Task 4: Verify Proxy Routing

**Check proxy-adapter logs:**
```bash
docker logs proxy-adapter 2>&1 | grep "proxy_routing"
```

**Expected for WEG:**
```
proxy_routing url=http://www.weg.net method=flaresolverr
proxy_routing url=http://www.weg.net/products method=flaresolverr
proxy_routing url=http://www.weg.net/about method=flaresolverr
...
```

**Expected for Brevini:**
```
proxy_routing url=http://www.brevini.com method=direct
proxy_routing url=http://www.brevini.com/products method=direct
...
```

---

### Task 5: Performance Testing

**Measure crawl performance:**

| Metric | Before | After | Notes |
|--------|--------|-------|-------|
| WEG pages crawled | 1 | 10+ | Success criteria |
| WEG crawl time | ~25s | ? | Measure with proxy |
| Brevini pages | 5 | 10+ | Should still work |
| Brevini crawl time | ~30s | ? | Should be similar |
| Proxy overhead | N/A | ? | Extra latency for blocked domains |

---

## Configuration Matrix

### Environment Variables

| Variable | Value | Where | Purpose |
|----------|-------|-------|---------|
| `PROXY_SERVER` | `http://proxy-adapter:8192` | Playwright | Browser proxy |
| `PROXY_USERNAME` | (none) | Playwright | Optional auth |
| `PROXY_PASSWORD` | (none) | Playwright | Optional auth |
| `FLARESOLVERR_URL` | `http://flaresolverr:8191` | Proxy Adapter | FlareSolverr backend |
| `FLARESOLVERR_BLOCKED_DOMAINS` | `weg.net,siemens.com,...` | Proxy Adapter | Routing logic |

---

## What Changes

### Before (Current State)
```
Playwright Browser
  ↓ (tries direct connection)
  ↓ BLOCKED by Akamai
  ↓
Falls back to Fetch engine
  ↓
Gets 1 page only (no link discovery)
```

### After (With PROXY_SERVER)
```
Playwright Browser
  ↓ PROXY_SERVER=http://proxy-adapter:8192
  ↓
Proxy Adapter
  ↓ (checks domain against blocked list)
  ├─→ Blocked domain → FlareSolverr → Success (10+ pages)
  └─→ Non-blocked → Direct → Success (10+ pages)
```

---

## Implementation Steps

### Step 1: Add Environment Variable (5 minutes)

```bash
# Edit docker-compose.yml
nano docker-compose.yml

# Add to playwright service:
environment:
  - PROXY_SERVER=http://proxy-adapter:8192
```

### Step 2: Rebuild and Deploy (2 minutes)

```bash
docker compose down
docker compose up -d --build playwright
```

### Step 3: Wait for Services (1 minute)

```bash
# Check Playwright is healthy
docker logs knowledge_extraction-orchestrator-playwright-1

# Should see:
# "Server is running on port 3003"
```

### Step 4: Test WEG Crawl (30 seconds)

```bash
# Create test project
PROJECT_ID=$(curl -s -X POST http://localhost:8000/api/v1/projects \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "WEG Proxy Test",
    "start_urls": ["http://www.weg.net"],
    "max_pages": 10,
    "extraction_schema": {"type": "object", "properties": {}}
  }' | jq -r '.id')

# Start crawl
curl -X POST http://localhost:8000/api/v1/crawl \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"http://www.weg.net\",
    \"project_id\": \"$PROJECT_ID\",
    \"company\": \"WEG\",
    \"max_depth\": 2,
    \"limit\": 10
  }"
```

### Step 5: Monitor Progress (2 minutes)

```bash
# Watch job status
watch -n 2 "curl -s http://localhost:8000/api/v1/crawl/<JOB_ID> \
  -H 'X-API-Key: $API_KEY' | jq '.pages_completed, .sources_created'"

# Watch proxy logs
docker logs -f proxy-adapter
```

### Step 6: Verify Results (1 minute)

```bash
# Should see 10 pages, not 1
curl -s http://localhost:8000/api/v1/crawl/<JOB_ID> \
  -H "X-API-Key: $API_KEY" | jq '{
    status: .status,
    pages_crawled: .pages_completed,
    sources_created: .sources_created,
    error: .error
  }'
```

**Total Time:** ~10 minutes

---

## Success Criteria

### Must Have ✅
- [ ] WEG crawls 10 pages (currently 1)
- [ ] Proxy-adapter logs show `method=flaresolverr` for WEG
- [ ] Brevini still crawls normally
- [ ] No errors in Playwright logs

### Nice to Have 🎯
- [ ] WEG crawl time < 60 seconds
- [ ] Proxy overhead < 10% for direct traffic
- [ ] FlareSolverr success rate > 95%

---

## Rollback Plan

If proxy configuration causes issues:

```bash
# 1. Edit docker-compose.yml
# Remove PROXY_SERVER environment variable

# 2. Restart services
docker compose up -d playwright

# 3. System reverts to previous behavior
# - Non-blocked domains: Playwright (works)
# - Blocked domains: Fetch fallback (1 page only)
```

---

## Known Limitations

### 1. HTTP Only for Blocked Domains
- **Issue:** FlareSolverr cannot proxy HTTPS to blocked domains
- **Workaround:** Use `http://www.weg.net` not `https://`
- **Impact:** Minimal (most sites redirect anyway)

### 2. Proxy Overhead
- **Issue:** Extra hop through proxy-adapter adds latency
- **Impact:** ~100-200ms per request
- **Mitigation:** Only applies to blocked domains

### 3. FlareSolverr Rate Limits
- **Issue:** FlareSolverr has built-in rate limiting
- **Impact:** May slow down aggressive crawls
- **Mitigation:** Respect crawl delays (already configured)

---

## Monitoring & Debugging

### Check Proxy is Being Used

```bash
# Playwright logs should show proxy configuration
docker exec knowledge_extraction-orchestrator-playwright-1 \
  printenv | grep PROXY_SERVER

# Should output:
# PROXY_SERVER=http://proxy-adapter:8192
```

### Monitor FlareSolverr Usage

```bash
# FlareSolverr logs
docker logs -f knowledge_extraction-orchestrator-flaresolverr-1

# Should see:
# "Solving challenge for http://www.weg.net"
# "Challenge solved successfully"
```

### Debug Proxy Routing

```bash
# Proxy adapter logs with context
docker logs proxy-adapter 2>&1 | grep -E "proxy_routing|solve_request|error"

# Expected for WEG:
# proxy_routing url=http://www.weg.net method=flaresolverr
# solve_request_start url=http://www.weg.net
# solve_request_success status=200 size=268KB
```

---

## Future Enhancements

### 1. Proxy Authentication (If Needed)
If proxy-adapter needs authentication:

```yaml
playwright:
  environment:
    - PROXY_SERVER=http://proxy-adapter:8192
    - PROXY_USERNAME=admin
    - PROXY_PASSWORD=${PROXY_PASSWORD}
```

### 2. Per-Domain Proxy Configuration
For advanced routing:

```yaml
playwright:
  environment:
    - PROXY_SERVER=http://proxy-adapter:8192
    - PROXY_BYPASS_DOMAINS=brevini.com,example.com
```

### 3. Proxy Health Checks
Monitor proxy availability:

```bash
# Add health check endpoint to proxy-adapter
curl http://localhost:8192/health
```

---

## Documentation Updates Needed

### 1. Update TRANSPARENT-PROXY-STATUS.md
- Change status from "Fork required" to "Environment variable only"
- Update effort from "1-2 weeks" to "1-2 hours"
- Celebrate the win! 🎉

### 2. Update FINDINGS-transparent-proxy.md
- Add section: "BREAKTHROUGH: Built-in Proxy Support"
- Explain why fork is no longer needed
- Keep iptables analysis for reference

### 3. Update stack.env
Add new optional variables:

```bash
# [OPTIONAL] Playwright Proxy Configuration
# PLAYWRIGHT_PROXY_SERVER=http://proxy-adapter:8192
# PLAYWRIGHT_PROXY_USERNAME=
# PLAYWRIGHT_PROXY_PASSWORD=
```

---

## Testing Checklist

### Pre-Deployment Tests
- [ ] docker-compose.yml syntax valid
- [ ] docker-compose.prod.yml syntax valid
- [ ] Environment variables set correctly
- [ ] Proxy-adapter service running
- [ ] FlareSolverr service running

### Post-Deployment Tests
- [ ] Playwright service starts successfully
- [ ] Playwright health check passes
- [ ] WEG crawl gets 10+ pages
- [ ] Brevini crawl still works
- [ ] Proxy logs show routing decisions
- [ ] FlareSolverr logs show challenge solving
- [ ] No errors in any service logs

### Integration Tests
- [ ] Create project via API
- [ ] Start WEG crawl
- [ ] Monitor progress
- [ ] Verify 10+ sources created
- [ ] Check content quality
- [ ] Verify outbound links discovered

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Proxy not working | Low | High | Fallback to fetch engine |
| Performance degradation | Medium | Low | Monitor metrics |
| FlareSolverr errors | Low | Medium | Error handling already in place |
| Config syntax error | Low | Low | Validate before deploy |
| Network connectivity | Low | High | Test in dev first |

**Overall Risk:** ✅ **LOW** - Simple config change with fallback

---

## Timeline

| Phase | Duration | Status |
|-------|----------|--------|
| Discovery | Complete | ✅ Done |
| Planning | Complete | ✅ Done |
| Implementation | 10 minutes | 🔄 Next |
| Testing | 15 minutes | ⏳ Pending |
| Documentation | 15 minutes | ⏳ Pending |
| **Total** | **40 minutes** | 🎯 Ready |

---

## Next Steps

1. ✅ Read this TODO (you're here!)
2. 🔄 Make the config change (5 minutes)
3. ⏳ Test WEG crawl (10 minutes)
4. ⏳ Update documentation (15 minutes)
5. 🎉 Celebrate not having to fork Firecrawl!

---

## References

- **Playwright Proxy API:** `apps/playwright-service-ts/api.ts` lines 18-123
- **Proxy Adapter:** `src/services/proxy/flaresolverr_adapter.py`
- **FlareSolverr Test:** Successfully fetched 268KB from WEG
- **Original Plan:** `docs/TODO-transparent-proxy.md` (superseded)
- **Findings:** `docs/FINDINGS-transparent-proxy.md` (update with this discovery)
