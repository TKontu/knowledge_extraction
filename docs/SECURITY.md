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
