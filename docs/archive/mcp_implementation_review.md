# Pipeline Review: MCP Server Implementation

## Flow

```
server.py → lifespan (client init)
  ↓
tools/*.py (15 tools) → client.py → HTTP → FastAPI API
  ↓
resources/templates.py (static resource)
  ↓
prompts/workflows.py (workflow templates)
```

## Critical (must fix)

- [x] **src/ke_mcp/client.py:37** - **Wrong authentication header** ✅ FIXED
  Was using `Authorization: Bearer` but API expects `X-API-Key` header.
  **Fixed in commit 43754b9** - Changed to `headers["X-API-Key"] = self.settings.api_key`

## Important (should fix)

- [x] **src/ke_mcp/client.py:86-89** - **500 errors not retried** ✅ FIXED
  Server errors (500+) now retry with exponential backoff, same as timeouts.

## Verified as Non-Issues (false positives)

The following were initially flagged but verified as working correctly:

1. **Empty string default for api_key** - Empty string `""` is falsy in Python, so `if self.settings.api_key:` correctly skips adding the header.

2. **get_job_status uses generic endpoint** - The generic `/jobs/{id}` endpoint returns a `result` dict containing all crawl-specific data. The tool correctly passes this through.

3. **No 401 handling** - 401 errors fall through to `response.raise_for_status()` which raises `HTTPStatusError`, then converted to `APIError`. Works correctly.

4. **Invalid Python syntax in prompts** - The f-string `{companies}` interpolates to `['Acme Inc', 'Competitor Corp']` which is valid Python syntax.

## Verified Correct

- API endpoint paths in client.py match actual FastAPI routes
- Report response field names match between client expectations and API responses
- Tool registration pattern is correct for FastMCP
- Lifespan context properly passes client to tools
- Error handling with APIError is consistent across all tools
- Authentication header now uses correct `X-API-Key` format
