# TODO: Transparent Proxy for Playwright Browsers

**Agent:** agent-transparent-proxy
**Branch:** `feat/transparent-proxy-playwright`
**Priority:** High

## Context

Playwright browsers make direct network connections, bypassing our proxy adapter at port 8192. The proxy adapter routes blocked domains (weg.net, siemens.com, wattdrive.com) through FlareSolverr to bypass Cloudflare protection.

**Current Issue:**
- HTTP_PROXY environment variables set on firecrawl-api service
- Playwright browsers launched by Firecrawl ignore these variables
- Browsers need explicit proxy configuration OR network-level interception
- We cannot modify Firecrawl/Playwright source code (using Docker images)

**Solution:**
Configure iptables NAT rules in the Playwright container to transparently redirect all outbound HTTP/HTTPS traffic (ports 80/443) to proxy-adapter:8192.

---

## Objective

Enable transparent network-level proxying so Playwright browser traffic routes through our proxy adapter, which then routes blocked domains to FlareSolverr for Cloudflare bypass.

---

## Tasks

### 1. Enhance Proxy Adapter - Add CONNECT Method Handler

**File:** `src/services/proxy/flaresolverr_adapter.py`

**Add method to `ProxyAdapter` class:**

```python
async def handle_connect(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Handle HTTPS CONNECT tunneling requests.

    CONNECT format: CONNECT example.com:443

    For blocked domains:
        Return 502 with message "HTTPS not supported for Cloudflare-protected domains. Use HTTP URLs."
        Log: https_blocked_domain_unsupported

    For non-blocked domains:
        Return 501 Not Implemented (CONNECT tunneling deferred to future work)
        OR implement bidirectional TCP tunnel (complex, see notes below)
    """
    # Parse target from path
    target = request.path.lstrip("/")
    if ":" not in target:
        return aiohttp.web.Response(status=400, text="Invalid CONNECT target")

    host, port_str = target.rsplit(":", 1)
    port = int(port_str)

    # Check if blocked domain
    if self.should_use_flaresolverr(host):
        logger.warning(
            "https_blocked_domain_unsupported",
            host=host,
            message="FlareSolverr cannot proxy HTTPS CONNECT"
        )
        return aiohttp.web.Response(
            status=502,
            text="HTTPS not supported for Cloudflare-protected domains. Use HTTP URLs."
        )

    # For non-blocked domains, return 501 Not Implemented (MVP)
    logger.info("connect_not_implemented", host=host, port=port)
    return aiohttp.web.Response(status=501, text="CONNECT method not yet implemented")
```

**Implementation Note:** Full CONNECT implementation requires bidirectional TCP proxying with asyncio streams. For MVP, return 501. HTTPS to non-blocked domains may fail, but most crawling uses HTTP.

### 2. Enhance Proxy Adapter - Update URL Extraction

**File:** `src/services/proxy/flaresolverr_adapter.py`

**Modify `handle_request` method:**

Add CONNECT handling and update URL extraction to support transparent proxy mode.

**Requirements:**
- Check if `request.method == "CONNECT"`, call `handle_connect()`
- Update URL extraction to handle both:
  - Explicit: `GET /http://example.com/path`
  - Transparent: `GET /path` with `Host: example.com` header
- For blocked domains + HTTPS URL, return 502 with message
- For blocked domains + HTTP URL, route to FlareSolverr
- For non-blocked domains, direct passthrough

**Add helper method:**

```python
def _extract_url(self, request: aiohttp.web.Request) -> str:
    """Extract target URL from request.

    Handles:
    - Explicit: GET /http://example.com/path -> http://example.com/path
    - Transparent: GET /path + Host: example.com -> http://example.com/path
    """
    path = request.path.lstrip("/")

    # Explicit proxy format
    if path.startswith(("http://", "https://")):
        return path

    # Transparent proxy format
    host = request.headers.get("Host")
    if not host:
        raise ValueError("Missing Host header in transparent proxy request")

    # Default to http:// (HTTPS uses CONNECT method)
    return f"http://{host}{request.path}"
```

**Update `handle_request`:**

```python
async def handle_request(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
    try:
        # Handle CONNECT for HTTPS
        if request.method == "CONNECT":
            return await self.handle_connect(request)

        # Extract URL (supports both explicit and transparent modes)
        url = self._extract_url(request)

        # Existing routing logic...
        parsed = urlparse(url)
        domain = parsed.netloc

        if self.should_use_flaresolverr(domain):
            # Block HTTPS to blocked domains
            if url.startswith("https://"):
                logger.warning("https_blocked_domain", domain=domain, url=url)
                return aiohttp.web.Response(
                    status=502,
                    text=f"HTTPS not supported for {domain}. Use HTTP URL."
                )

            # Route HTTP through FlareSolverr
            logger.info("proxy_routing", url=url, method="flaresolverr")
            response = await self.flaresolverr_client.solve_request(url)
            return aiohttp.web.Response(
                text=response.html,
                status=response.status,
                headers=response.headers
            )
        else:
            # Direct passthrough
            logger.info("proxy_routing", url=url, method="direct")
            async with httpx.AsyncClient() as client:
                http_response = await client.get(url)
                return aiohttp.web.Response(
                    body=http_response.content,
                    status=http_response.status_code,
                    headers=dict(http_response.headers),
                )

    except Exception as e:
        logger.error("proxy_error", error=str(e), path=request.path)
        return aiohttp.web.Response(text=str(e), status=500)
```

### 3. Create Playwright Entrypoint Script

**File:** `docker/playwright-entrypoint.sh` (new file)

**Content:**

```bash
#!/bin/bash
set -e

echo "=== Configuring transparent proxy for Playwright ==="

# Proxy configuration
PROXY_HOST="proxy-adapter"
PROXY_PORT="8192"

# Install iptables if not present
if ! command -v iptables &> /dev/null; then
    echo "Installing iptables..."
    apt-get update -qq && apt-get install -y iptables > /dev/null
fi

# Flush existing NAT rules
iptables -t nat -F OUTPUT 2>/dev/null || true

# Exclude internal Docker networks (preserve internal service communication)
iptables -t nat -A OUTPUT -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -d 10.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -d 172.16.0.0/12 -j RETURN
iptables -t nat -A OUTPUT -d 192.168.0.0/16 -j RETURN

# Redirect HTTP (port 80) to proxy
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -j DNAT --to-destination ${PROXY_HOST}:${PROXY_PORT}

# Redirect HTTPS (port 443) to proxy
iptables -t nat -A OUTPUT -p tcp --dport 443 \
    -j DNAT --to-destination ${PROXY_HOST}:${PROXY_PORT}

# Display configured rules for debugging
echo "=== iptables NAT rules ==="
iptables -t nat -L OUTPUT -n -v

# Execute original Playwright entrypoint
echo "=== Starting Playwright service ==="
exec docker-entrypoint.sh "$@"
```

**After creating file:**
```bash
chmod +x docker/playwright-entrypoint.sh
```

### 4. Update Docker Compose - Playwright Service

**File:** `docker-compose.prod.yml`

**Modify `playwright` service (around line 43):**

```yaml
playwright:
  image: ghcr.io/firecrawl/playwright-service:latest
  # Add NET_ADMIN capability for iptables
  cap_add:
    - NET_ADMIN
  # Mount custom entrypoint script
  volumes:
    - ./docker/playwright-entrypoint.sh:/usr/local/bin/playwright-entrypoint.sh:ro
  # Use custom entrypoint
  entrypoint: ["/usr/local/bin/playwright-entrypoint.sh"]
  # Ensure proxy-adapter starts first
  depends_on:
    - proxy-adapter
  restart: unless-stopped
  networks:
    - scristill
  deploy:
    resources:
      limits:
        memory: 1G
        cpus: '1'
      reservations:
        memory: 256M
```

**Apply same changes to `docker-compose.yml` (dev environment).**

### 5. Write Tests

**File:** `tests/test_proxy_transparent.py` (new file)

**Test cases:**

```python
import pytest
from unittest.mock import AsyncMock, Mock
from aiohttp.test_utils import TestClient, TestServer
import aiohttp.web

from src.services.proxy.flaresolverr_adapter import ProxyAdapter


@pytest.fixture
def proxy_adapter():
    """Create ProxyAdapter instance for testing."""
    return ProxyAdapter(
        flaresolverr_url="http://flaresolverr:8191",
        blocked_domains=["weg.net", "siemens.com", "wattdrive.com"],
        max_timeout=60000
    )


class TestURLExtraction:
    """Test URL extraction in transparent and explicit proxy modes."""

    def test_extract_url_explicit_http(self, proxy_adapter):
        """Test explicit proxy format: GET /http://example.com/path"""
        request = Mock()
        request.path = "/http://example.com/products"
        request.headers = {}

        url = proxy_adapter._extract_url(request)
        assert url == "http://example.com/products"

    def test_extract_url_transparent_with_host_header(self, proxy_adapter):
        """Test transparent proxy format: GET /path + Host header"""
        request = Mock()
        request.path = "/products"
        request.headers = {"Host": "example.com"}

        url = proxy_adapter._extract_url(request)
        assert url == "http://example.com/products"

    def test_extract_url_missing_host_header_raises_error(self, proxy_adapter):
        """Test error when Host header missing in transparent mode"""
        request = Mock()
        request.path = "/products"
        request.headers = {}

        with pytest.raises(ValueError, match="Missing Host header"):
            proxy_adapter._extract_url(request)


class TestCONNECTHandling:
    """Test HTTPS CONNECT method handling."""

    @pytest.mark.asyncio
    async def test_connect_blocked_domain_returns_502(self, proxy_adapter):
        """Test CONNECT to blocked domain returns 502 error"""
        request = Mock()
        request.method = "CONNECT"
        request.path = "/weg.net:443"

        response = await proxy_adapter.handle_connect(request)

        assert response.status == 502
        assert "HTTPS not supported" in response.text

    @pytest.mark.asyncio
    async def test_connect_non_blocked_domain_returns_501(self, proxy_adapter):
        """Test CONNECT to non-blocked domain returns 501 (not implemented)"""
        request = Mock()
        request.method = "CONNECT"
        request.path = "/example.com:443"

        response = await proxy_adapter.handle_connect(request)

        assert response.status == 501

    @pytest.mark.asyncio
    async def test_connect_invalid_target_returns_400(self, proxy_adapter):
        """Test CONNECT with invalid target returns 400"""
        request = Mock()
        request.method = "CONNECT"
        request.path = "/invalid-target-no-port"

        response = await proxy_adapter.handle_connect(request)

        assert response.status == 400


class TestHTTPSBlockedDomains:
    """Test HTTPS URL handling for blocked domains."""

    @pytest.mark.asyncio
    async def test_https_url_to_blocked_domain_returns_502(self, proxy_adapter):
        """Test HTTPS URL to blocked domain returns error"""
        request = Mock()
        request.method = "GET"
        request.path = "/https://www.weg.net/"
        request.headers = {}

        response = await proxy_adapter.handle_request(request)

        assert response.status == 502
        assert "Use HTTP URL" in response.text
```

### 6. Integration Testing

**After deployment, verify with these commands:**

```bash
# Build and deploy
docker compose -f docker-compose.prod.yml build proxy-adapter
docker compose -f docker-compose.prod.yml up -d --force-recreate playwright

# Verify iptables rules
docker compose -f docker-compose.prod.yml exec playwright iptables -t nat -L OUTPUT -n -v

# Test HTTP request from Playwright container
docker compose -f docker-compose.prod.yml exec playwright curl -v http://www.example.com

# Monitor proxy logs
docker compose -f docker-compose.prod.yml logs -f proxy-adapter

# Test crawl to non-blocked domain
curl -X POST http://localhost:8742/api/v1/crawl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: thisismyapikey3215215632" \
  -d '{
    "url": "http://www.brevinipowertransmission.com/",
    "project_id": "02d5d1b2-1efd-4bce-92bd-cf1a80fd3b98",
    "company": "brevini_transparent_test",
    "max_depth": 1,
    "limit": 5,
    "auto_extract": false
  }'

# Test crawl to blocked domain (USE HTTP!)
curl -X POST http://localhost:8742/api/v1/crawl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: thisismyapikey3215215632" \
  -d '{
    "url": "http://www.weg.net/",
    "project_id": "02d5d1b2-1efd-4bce-92bd-cf1a80fd3b98",
    "company": "weg_transparent_test",
    "max_depth": 1,
    "limit": 5,
    "auto_extract": false
  }'
```

---

## Constraints

- **No Firecrawl/Playwright source code changes** - Using official Docker images
- **Preserve existing proxy adapter routing logic** - Domain-based routing to FlareSolverr
- **Docker Compose deployment** - All changes via compose configuration
- **Testable locally** - Full integration test suite before production
- **Rollback-friendly** - Can disable iptables or revert compose changes quickly
- **Do NOT run full test suite** - Only run tests in Test Scope below
- **Do NOT lint entire codebase** - Only lint files in Lint Scope below

---

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_proxy_transparent.py -v
```

---

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/services/proxy/flaresolverr_adapter.py \
  tests/test_proxy_transparent.py
```

---

## Verification

Before creating PR, run ONLY the scoped commands above:

1. `pytest tests/test_proxy_transparent.py -v` - Must pass
2. `ruff check <files listed above>` - Must be clean
3. All 6 tasks above completed
4. iptables rules configured correctly in Playwright container
5. Proxy logs show routing decisions (method=flaresolverr for blocked, method=direct for others)
6. Crawl to non-blocked domain succeeds
7. Crawl to blocked domain (HTTP URL) succeeds with sources_created > 0

---

## Definition of Done

- [ ] All 6 tasks completed
- [ ] Tests written and passing (scoped)
- [ ] Lint clean (scoped)
- [ ] iptables rules verified in Playwright container
- [ ] Proxy routing logs show correct decisions
- [ ] Non-blocked domain crawl works (sources > 0)
- [ ] Blocked domain crawl works via FlareSolverr (HTTP URL, sources > 0)
- [ ] HTTPS to blocked domains returns clear error message
- [ ] PR created with title: `feat: transparent proxy for Playwright browsers via iptables`

---

## Success Criteria

- Playwright container launches with iptables NAT rules configured
- HTTP traffic from Playwright routes through proxy adapter (visible in logs)
- Blocked domains route to FlareSolverr (method=flaresolverr in logs)
- Non-blocked domains use direct passthrough (method=direct in logs)
- Crawl jobs to blocked domains succeed (sources_created > 0)
- Internal Docker service communication bypasses proxy
- No performance regression for non-blocked domains

---

## Known Limitations

### HTTPS to Blocked Domains Not Supported

**Issue:** FlareSolverr requires explicit HTTP POST requests with URLs. It cannot act as a CONNECT tunnel for encrypted HTTPS traffic.

**Workaround:** Use HTTP URLs when crawling Cloudflare-protected domains.

**Example:**
```bash
# ✅ Works - HTTP URL
POST /api/v1/crawl {"url": "http://www.weg.net/"}

# ❌ Fails - HTTPS URL
POST /api/v1/crawl {"url": "https://www.weg.net/"}
```

**Future Enhancement:** Implement MITM proxy with custom CA certificate to intercept HTTPS. This is complex and has security/trust implications.

---

## Rollback Strategy

**Quick disable (no code changes):**
```bash
docker compose -f docker-compose.prod.yml exec playwright iptables -t nat -F
```

**Full rollback:**
```bash
# Revert docker-compose changes
git checkout docker-compose.prod.yml docker-compose.yml

# Restart Playwright without custom entrypoint
docker compose -f docker-compose.prod.yml up -d --force-recreate playwright
```

**Preserve logs before rollback:**
```bash
docker compose -f docker-compose.prod.yml logs proxy-adapter > rollback-proxy-logs.txt
docker compose -f docker-compose.prod.yml logs playwright > rollback-playwright-logs.txt
```

---

## Notes

**Why iptables NAT?**
- Truly transparent - no application changes needed
- Works at network layer - intercepts all TCP connections
- Standard approach for container-based proxying
- Used in production by many companies (Istio, Linkerd, etc.)

**Why NET_ADMIN capability?**
- Required for iptables rule manipulation
- Scoped to Playwright container only
- Standard security practice for network proxying

**Security:**
- iptables rules exclude internal networks (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Preserves Playwright→Firecrawl API communication
- All routing decisions logged for audit trail

**Performance:**
- Direct passthrough adds ~1-5ms latency
- FlareSolverr routing adds ~10-15s (browser rendering)
- iptables overhead is negligible (<1ms)
