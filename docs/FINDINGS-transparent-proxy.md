# Transparent Proxy Implementation - Findings & Limitations

**Date:** 2026-01-16
**Status:** ⚠️ Partially Working - Critical Limitation Discovered

---

## Executive Summary

The transparent proxy implementation using iptables NAT is **technically correct** but **fundamentally incompatible** with Playwright browsers. While the infrastructure works perfectly for direct HTTP requests, Playwright's Chromium browser bypasses iptables OUTPUT chain rules.

### What Works ✅

1. **Direct HTTP Requests** (fetch engine)
   - Non-JavaScript sites crawl successfully
   - iptables NAT would route these (but not needed - fetch uses HTTP_PROXY env var)

2. **Non-Blocked Domains with Playwright**
   - Sites without Cloudflare/bot protection work fine
   - No proxy needed

3. **Infrastructure Components**
   - iptables rules correctly configured
   - Proxy adapter with transparent mode support
   - FlareSolverr integration
   - Docker networking

### What Doesn't Work ❌

1. **Playwright Browser Traffic**
   - Chromium browser processes **bypass iptables NAT**
   - Zero packets captured in iptables counters
   - Browser connections don't go through OUTPUT chain

2. **Cloudflare-Protected Sites via Playwright**
   - Cannot route browser traffic to FlareSolverr
   - Falls back to fetch engine (limited crawling capability)
   - Only gets single page instead of full crawl

---

## Technical Analysis

### Why Playwright Bypasses iptables

Playwright launches **browser processes** that:

1. **Run in separate process space** from the Node.js application
2. **Create direct socket connections** that bypass standard OUTPUT chain
3. **Use browser networking stack** (not system networking)
4. **Require explicit proxy configuration** in launch options

### Evidence from Testing

```bash
# iptables packet counters after crawl
Chain OUTPUT (policy ACCEPT 0 packets, 0 bytes)
 pkts bytes target     prot opt in     out     source               destination
    0     0 RETURN     6    --  *      *       0.0.0.0/0            127.0.0.0/8          tcp dpt:80
    0     0 RETURN     6    --  *      *       0.0.0.0/0            172.19.0.0/16        tcp dpt:80
    0     0 DNAT       6    --  *      *       0.0.0.0/0            0.0.0.0/0            tcp dpt:80 to:172.19.0.3:8192
```

**Zero packets matched** = iptables never sees Chromium's traffic

### Firecrawl Behavior

When Playwright fails, Firecrawl automatically falls back:

```
Playwright → 500 error "An error occurred while fetching the page"
↓
Waterfalling to next engine...
↓
Fetch engine → Success (but limited - no JS rendering, no link discovery)
```

**Result:** Only 1 page scraped instead of 5+ with full crawl

---

## What Was Implemented

### 1. Enhanced Proxy Adapter (`src/services/proxy/flaresolverr_adapter.py`)

Added transparent proxy support:

```python
def _extract_url(self, request: aiohttp.web.Request) -> str:
    """Extract URL from both explicit and transparent proxy formats."""
    path = request.path.lstrip("/")

    # Explicit: GET /http://example.com/
    if path.startswith(("http://", "https://")):
        return path

    # Transparent: GET /path with Host: example.com
    host = request.headers.get("Host", "")
    if host:
        return f"http://{host}{request.path}"
```

**Status:** ✅ Working (but not receiving Playwright traffic)

### 2. iptables NAT Configuration (`docker/playwright-entrypoint.sh`)

Redirects HTTP/HTTPS traffic to proxy adapter:

```bash
# Detect Docker subnet from proxy IP
DOCKER_SUBNET=$(echo ${PROXY_IP} | awk -F. '{print $1"."$2".0.0/16"}')

# Redirect external HTTP traffic
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -d ${DOCKER_SUBNET} -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -j DNAT --to-destination ${PROXY_IP}:${PROXY_PORT}
```

**Status:** ✅ Correctly configured (but Chromium bypasses it)

### 3. Custom Playwright Dockerfile (`Dockerfile.playwright`)

Installs iptables and custom entrypoint:

```dockerfile
FROM ghcr.io/firecrawl/playwright-service:latest
USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends iptables && \
    apt-get clean
COPY docker/playwright-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["npm", "start"]
```

**Status:** ✅ Working

### 4. Comprehensive Tests (`tests/test_proxy_transparent.py`)

183 lines of tests covering:
- URL extraction (both formats)
- CONNECT method handling
- HTTPS blocking for blocked domains
- Subdomain matching

**Status:** ✅ All pass

---

## Alternative Approaches

### Option 1: Fork Firecrawl ⭐ **RECOMMENDED**

**What:** Modify Firecrawl to pass proxy to Playwright launch options

**Implementation:**
```javascript
// In Firecrawl's Playwright engine
const browser = await playwright.chromium.launch({
  proxy: {
    server: 'http://proxy-adapter:8192'
  }
});
```

**Pros:**
- Actually works for Playwright
- Full crawling capability with FlareSolverr
- Clean architecture

**Cons:**
- Requires maintaining forked Firecrawl
- Updates need manual merging

**Effort:** Medium (1-2 days)

---

### Option 2: Use FlareSolverr Directly (Current Fallback)

**What:** Bypass Playwright, use fetch engine + FlareSolverr for blocked domains

**How it works:**
1. Firecrawl tries Playwright → fails
2. Falls back to fetch engine
3. Fetch engine hits Cloudflare → blocked
4. Falls back to FlareSolverr (stealthProxy flag)

**Pros:**
- Already working (partially)
- No code changes needed

**Cons:**
- **Only gets 1 page** (no link discovery)
- No JavaScript rendering for non-blocked domains
- Inefficient (multiple fallbacks)

**Current Status:** This is what's happening now

---

### Option 3: Hybrid Approach

**What:** Use Playwright for non-blocked, FlareSolverr API directly for blocked

**Implementation:**
```python
# In crawl worker
if domain in blocked_domains:
    # Call FlareSolverr API directly
    content = await flaresolverr_client.solve_request(url)
    links = extract_links_from_html(content)
    # Queue discovered links
else:
    # Use Firecrawl with Playwright (normal)
    result = await firecrawl.scrape(url)
```

**Pros:**
- Best of both worlds
- Full control over crawling logic
- Can implement custom link discovery

**Cons:**
- More complex crawling logic
- Need to handle link extraction ourselves
- Duplicate functionality with Firecrawl

**Effort:** Medium-High (2-3 days)

---

### Option 4: Use Different Browser Automation

**What:** Replace Playwright with Selenium + proxy support

**Pros:**
- Selenium has built-in proxy support
- Works with iptables better

**Cons:**
- Requires forking/replacing Firecrawl entirely
- Selenium is slower than Playwright
- Losing Firecrawl's features

**Effort:** High (1 week+)

---

## Recommendation

### Short Term (Now)

**Accept current behavior:**
- Non-blocked domains: Full Playwright crawling ✅
- Blocked domains: Single page via FlareSolverr fallback ⚠️

**Document limitation** in user-facing docs and adjust expectations.

### Medium Term (1-2 weeks)

**Implement Option 1: Fork Firecrawl**

1. Fork `ghcr.io/firecrawl/firecrawl`
2. Add proxy support to Playwright launch options
3. Build custom image: `scristill/firecrawl:proxy-enabled`
4. Update docker-compose to use custom image

**This gives us:**
- Full Playwright crawling with FlareSolverr for all domains
- 5+ pages from Cloudflare-protected sites
- Proper link discovery and depth traversal

### Long Term (Future)

Consider **Option 3 (Hybrid)** if we need more control over:
- Custom crawling logic
- Domain-specific strategies
- Advanced link filtering

---

## Testing Results

### Brevini (Non-Blocked) ✅
```
URL: http://www.brevini.com
Result: 5 pages crawled, 5 sources created
Engine: Playwright (no proxy needed)
Status: SUCCESS
```

### WEG (Blocked - Cloudflare) ⚠️
```
URL: http://www.weg.net
Result: 1 page crawled, 1 source created
Engine: Fetch → FlareSolverr fallback
Status: PARTIAL (got content, but no crawling)
```

### HTTPBin (Test Site) ✅
```
URL: http://httpbin.org
Result: 1 page crawled, 1 source created
Engine: Fetch (simple HTTP, no JS needed)
Status: SUCCESS
```

---

## Files Modified

### Infrastructure
- `docker/playwright-entrypoint.sh` - iptables NAT setup
- `Dockerfile.playwright` - Custom Playwright image with iptables
- `docker-compose.yml` - NET_ADMIN capability, custom build
- `docker-compose.prod.yml` - NET_ADMIN capability, custom build

### Application Code
- `src/services/proxy/flaresolverr_adapter.py` - Transparent proxy support
  - `_extract_url()` - URL extraction from Host header
  - `handle_connect()` - HTTPS CONNECT handling
  - Enhanced `handle_request()` - Routes CONNECT, blocks HTTPS to blocked domains

### Tests
- `tests/test_proxy_transparent.py` - 183 lines, 15 test cases

### Configuration
- Fixed `PLAYWRIGHT_MICROSERVICE_URL` to include `/scrape` endpoint

---

## Known Issues

### Issue 1: Playwright Browsers Bypass iptables
- **Severity:** Critical
- **Impact:** Cannot proxy Playwright traffic to FlareSolverr
- **Workaround:** Falls back to fetch engine (limited)
- **Fix:** Fork Firecrawl (Option 1)

### Issue 2: HTTPS to Blocked Domains Not Supported
- **Severity:** Medium
- **Impact:** Must use HTTP URLs for FlareSolverr
- **Cause:** FlareSolverr cannot act as HTTPS CONNECT tunnel
- **Workaround:** Use `http://` URLs explicitly

### Issue 3: Limited Crawling for Cloudflare Sites
- **Severity:** Medium
- **Impact:** Only 1 page instead of 5+
- **Cause:** Fetch engine doesn't discover links
- **Workaround:** Accept limitation or implement Option 1

---

## Lessons Learned

1. **iptables NAT doesn't work for browser automation tools**
   - Browsers create connections that bypass standard OUTPUT chain
   - Always test with actual browser traffic, not curl

2. **HTTP_PROXY environment variables ignored by Playwright**
   - Playwright needs explicit proxy in launch options
   - Can't be configured externally

3. **Firecrawl's fallback behavior is good**
   - Automatically tries multiple engines
   - Gets *some* content even when Playwright fails
   - But limited crawling capability

4. **Docker networking considerations**
   - Must exclude Docker subnet from iptables rules
   - Need to dynamically detect subnet (172.19.0.0/16 in our case)

---

## Next Steps

1. **Decision Required:** Choose approach (Option 1, 2, or 3)
2. **Update Documentation:** Document current limitations for users
3. **If Option 1:** Set up Firecrawl fork and CI/CD for custom image
4. **Testing:** Comprehensive testing after any changes

---

## References

- Original Plan: `docs/TODO-transparent-proxy.md`
- Proxy Adapter: `src/services/proxy/flaresolverr_adapter.py:66-109`
- iptables Setup: `docker/playwright-entrypoint.sh:32-61`
- Tests: `tests/test_proxy_transparent.py`
- FlareSolverr Docs: https://github.com/FlareSolverr/FlareSolverr
