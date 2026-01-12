# TODO: Security Hardening

**Agent ID**: `agent-security`
**Branch**: `feat/security-hardening`
**Priority**: 4

## Objective

Remove insecure default API key, add HTTPS enforcement option, and document security best practices.

## Context

- `src/config.py` line 16-19 has default API key "dev-key-change-in-production"
- API key auth in `src/middleware/auth.py` works but allows insecure default
- No HTTPS enforcement option exists
- No security documentation in README or dedicated docs
- Rate limiting middleware already exists (`middleware/rate_limit.py`)

## Tasks

### 1. Remove default API key

**File**: `src/config.py`

Change the API key field to require explicit configuration:

```python
# Before:
api_key: str = Field(
    default="dev-key-change-in-production",
    description="API key for authentication",
)

# After:
api_key: str = Field(
    description="API key for authentication (required - no default)",
)

@field_validator("api_key")
@classmethod
def validate_api_key(cls, v: str) -> str:
    """Validate API key is set and not a known insecure value."""
    insecure_values = {
        "dev-key-change-in-production",
        "changeme",
        "test",
        "dev",
        "development",
    }
    if not v:
        raise ValueError("API_KEY environment variable must be set")
    if v.lower() in insecure_values:
        raise ValueError(
            f"Insecure API key '{v}'. Please set a strong API_KEY in production."
        )
    if len(v) < 16:
        raise ValueError("API key must be at least 16 characters")
    return v
```

### 2. Add startup security check

**File**: `src/main.py`

Add security validation on startup:

```python
import warnings
from config import settings

# In lifespan or startup, add warning for development mode
def check_security_config():
    """Log security configuration status."""
    issues = []

    # Check API key strength
    if len(settings.api_key) < 32:
        issues.append("API key is shorter than recommended (32+ characters)")

    # Check HTTPS enforcement
    if not settings.enforce_https:
        issues.append("HTTPS enforcement is disabled")

    if issues:
        for issue in issues:
            logger.warning("security_config_issue", issue=issue)
    else:
        logger.info("security_config_valid")
```

### 3. Add HTTPS enforcement option

**File**: `src/config.py`

Add HTTPS configuration:

```python
# Security - HTTPS
enforce_https: bool = Field(
    default=False,
    description="Redirect HTTP to HTTPS (enable in production)",
)
https_redirect_host: str | None = Field(
    default=None,
    description="Host to redirect to for HTTPS (optional)",
)
```

**File**: `src/middleware/https.py` (new file)

```python
"""HTTPS enforcement middleware."""

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect HTTP requests to HTTPS."""

    async def dispatch(self, request: Request, call_next):
        """Check for HTTPS and redirect if needed."""
        if not settings.enforce_https:
            return await call_next(request)

        # Check if request is already HTTPS
        # X-Forwarded-Proto is set by reverse proxies
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)

        if proto != "https":
            # Build HTTPS URL
            host = settings.https_redirect_host or request.url.netloc
            https_url = request.url.replace(scheme="https", netloc=host)

            return RedirectResponse(
                url=str(https_url),
                status_code=301,
            )

        return await call_next(request)
```

**File**: `src/main.py`

Add middleware:

```python
from middleware.https import HTTPSRedirectMiddleware

# Add before other middleware
if settings.enforce_https:
    app.add_middleware(HTTPSRedirectMiddleware)
```

### 4. Add security headers middleware

**File**: `src/middleware/security_headers.py` (new file)

```python
"""Security headers middleware."""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        """Add security headers to response."""
        response = await call_next(request)

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Enable XSS filter
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy (API-focused)
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

        return response
```

**File**: `src/main.py`

Add middleware:

```python
from middleware.security_headers import SecurityHeadersMiddleware

app.add_middleware(SecurityHeadersMiddleware)
```

### 5. Create security documentation

**File**: `docs/SECURITY.md` (new file)

```markdown
# Security Guide

## Authentication

All API endpoints (except health, metrics, docs) require API key authentication.

### API Key Configuration

Set the `API_KEY` environment variable:

```bash
# Generate a secure key
openssl rand -hex 32

# Set in environment
export API_KEY="your-generated-key-here"
```

**Requirements:**
- Minimum 16 characters (32+ recommended)
- Cannot use known insecure values (dev, test, changeme, etc.)
- Must be set explicitly (no default)

### Using the API Key

Include in request header:

```bash
curl -H "X-API-Key: your-key" http://localhost:8000/api/v1/projects
```

## HTTPS

### Enabling HTTPS Enforcement

For production, enable HTTPS redirect:

```bash
ENFORCE_HTTPS=true
HTTPS_REDIRECT_HOST=api.yourdomain.com
```

This redirects all HTTP requests to HTTPS.

### Recommended Setup

Use a reverse proxy (nginx, Caddy, Traefik) for TLS termination:

```
Client → HTTPS → Reverse Proxy → HTTP → Pipeline Service
```

Example nginx config:

```nginx
server {
    listen 443 ssl;
    server_name api.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Host $host;
    }
}
```

## Rate Limiting

Rate limiting is enabled by default:

- **100 requests per minute** per API key
- Returns `429 Too Many Requests` when exceeded
- Includes `X-RateLimit-*` headers in responses

Configure via environment:

```bash
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_WINDOW_SECONDS=60
```

## Security Headers

All responses include security headers:

- `X-Frame-Options: DENY` - Prevents clickjacking
- `X-Content-Type-Options: nosniff` - Prevents MIME sniffing
- `X-XSS-Protection: 1; mode=block` - XSS protection
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy: default-src 'none'`

## Database Security

- Use strong passwords for PostgreSQL
- Restrict database network access
- Enable SSL for database connections in production

```bash
DATABASE_URL=postgresql://user:strongpassword@host:5432/db?sslmode=require
```

## Redis Security

- Use password authentication
- Restrict network access

```bash
REDIS_URL=redis://:password@host:6379
```

## Best Practices

1. **Never commit secrets** - Use environment variables or secret management
2. **Rotate API keys** - Change keys periodically
3. **Use HTTPS** - Always in production
4. **Monitor logs** - Watch for authentication failures
5. **Keep dependencies updated** - Regular security patches
6. **Network isolation** - Restrict service communication

## Reporting Security Issues

Report security vulnerabilities to: security@yourdomain.com

Do not open public issues for security bugs.
```

### 6. Update .env.example

**File**: `.env.example`

Update with security warnings:

```bash
# ===================
# SECURITY (REQUIRED)
# ===================
# IMPORTANT: You MUST set a strong API key
# Generate with: openssl rand -hex 32
API_KEY=

# HTTPS enforcement (enable in production)
ENFORCE_HTTPS=false
# HTTPS_REDIRECT_HOST=api.yourdomain.com

# Rate limiting
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_WINDOW_SECONDS=60
```

### 7. Write tests

**File**: `tests/test_security.py`

```python
"""Tests for security configuration and middleware."""

import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError


class TestAPIKeyValidation:
    def test_empty_api_key_raises_error(self):
        """Empty API key should fail validation."""
        from config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(api_key="")

        assert "API_KEY" in str(exc_info.value)

    def test_insecure_api_key_raises_error(self):
        """Known insecure API keys should fail."""
        from config import Settings

        insecure_keys = ["dev", "test", "changeme", "dev-key-change-in-production"]

        for key in insecure_keys:
            with pytest.raises(ValidationError):
                Settings(api_key=key)

    def test_short_api_key_raises_error(self):
        """API key shorter than 16 chars should fail."""
        from config import Settings

        with pytest.raises(ValidationError):
            Settings(api_key="short")

    def test_valid_api_key_passes(self):
        """Valid API key should pass."""
        from config import Settings

        settings = Settings(api_key="a" * 32)
        assert settings.api_key == "a" * 32


class TestSecurityHeaders:
    @pytest.mark.asyncio
    async def test_security_headers_added(self, client):
        """All responses should have security headers."""
        response = await client.get("/health")

        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert "X-XSS-Protection" in response.headers


class TestHTTPSRedirect:
    @pytest.mark.asyncio
    async def test_no_redirect_when_disabled(self, client):
        """Should not redirect when HTTPS enforcement disabled."""
        with patch("config.settings.enforce_https", False):
            response = await client.get("/health")
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_redirect_when_enabled_and_http(self, client):
        """Should redirect HTTP to HTTPS when enforcement enabled."""
        with patch("config.settings.enforce_https", True):
            with patch("config.settings.https_redirect_host", "secure.example.com"):
                response = await client.get(
                    "/health",
                    headers={"X-Forwarded-Proto": "http"},
                    follow_redirects=False,
                )
                assert response.status_code == 301
                assert "https://" in response.headers.get("location", "")


class TestSecurityDocumentation:
    def test_security_docs_exist(self):
        """Security documentation should exist."""
        from pathlib import Path

        docs_path = Path(__file__).parent.parent / "docs" / "SECURITY.md"
        assert docs_path.exists(), "docs/SECURITY.md should exist"

    def test_security_docs_has_required_sections(self):
        """Security docs should have key sections."""
        from pathlib import Path

        docs_path = Path(__file__).parent.parent / "docs" / "SECURITY.md"
        content = docs_path.read_text()

        required_sections = [
            "Authentication",
            "API Key",
            "HTTPS",
            "Rate Limiting",
            "Best Practices",
        ]

        for section in required_sections:
            assert section in content, f"Missing section: {section}"
```

## Constraints

- Do NOT break existing API key authentication
- Do NOT change rate limiting logic (already implemented)
- HTTPS redirect should be opt-in (disabled by default)
- Keep backward compatibility for development setups
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_security.py tests/test_auth_middleware.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/config.py src/middleware/auth.py src/middleware/https.py src/middleware/security_headers.py src/main.py
```

## Verification

Before creating PR, run ONLY the scoped commands above:

1. `pytest tests/test_security.py tests/test_auth_middleware.py -v` - Must pass
2. `ruff check src/config.py src/middleware/auth.py src/middleware/https.py src/middleware/security_headers.py src/main.py` - Must be clean
3. All tasks above completed

## Definition of Done

- [ ] Default API key removed, validation added
- [ ] HTTPS redirect middleware created and wired
- [ ] Security headers middleware created and wired
- [ ] Startup security check added
- [ ] `docs/SECURITY.md` created
- [ ] `.env.example` updated with security section
- [ ] Tests written and passing
- [ ] PR created with title: `feat: security hardening and documentation`
