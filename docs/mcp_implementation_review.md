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

- [ ] **src/ke_mcp/client.py:37** - **Wrong authentication header**
  ```python
  headers["Authorization"] = f"Bearer {self.settings.api_key}"
  ```
  API expects `X-API-Key` header, not `Authorization: Bearer` (see `src/middleware/auth.py:33`).
  **Impact**: All authenticated API calls will fail with 401 Unauthorized.
  **Fix**: Change to `headers["X-API-Key"] = self.settings.api_key`

## Important (should fix)

- [ ] **src/ke_mcp/tools/reports.py:124** - **Field mismatch in get_report**
  Client expects `generated_at` but API returns it as `generated_at` field. Actually OK.
  However, the API `ReportResponse.generated_at` at line 196 returns `report.created_at.isoformat()` - this is correct.

- [ ] **src/ke_mcp/client.py:86-89** - **500 errors not retried**
  Server errors (500+) raise immediately instead of retrying like timeouts. This is inconsistent - transient server errors should also retry.
  ```python
  elif response.status_code >= 500:
      raise APIError(...)  # Should retry instead
  ```

- [ ] **src/ke_mcp/tools/acquisition.py:170-201** - **get_job_status uses generic job endpoint**
  The tool calls `client.get_job()` which hits `/api/v1/jobs/{job_id}`, but crawl/scrape jobs should use `/api/v1/crawl/{job_id}` or `/api/v1/scrape/{job_id}` respectively. The generic job endpoint may not have the same response structure.

## Minor

- [ ] **src/ke_mcp/config.py:48-50** - **Empty string as default for api_key**
  Default is `""` which will pass truthiness check but fail auth. Consider using `None` as default and checking explicitly.
  ```python
  api_key: str = Field(default="", ...)  # Empty string is truthy-ish but invalid
  ```

- [ ] **src/ke_mcp/client.py:162-163** - **params handling for details=False**
  When `details=False`, params is set to `None`. This works but is inconsistent - could pass `{"details": "false"}` for explicitness.

- [ ] **src/ke_mcp/prompts/workflows.py:98-99, 115-116** - **Invalid Python syntax in prompt templates**
  The prompts show `source_groups={companies}` which is invalid Python (uses raw variable, not JSON).
  Should be `source_groups=["Company A", "Company B"]` or similar valid format.

- [ ] **No 401 handling in _request()** - Client handles 404, 409, 422, 500+ but not 401 (authentication failures). Should map 401 to a specific error or log it clearly.

## Not Issues (verified correct)

- API endpoint paths in client.py match actual FastAPI routes
- Report response field names match between client expectations and API responses
- Tool registration pattern is correct for FastMCP
- Lifespan context properly passes client to tools
- Error handling with APIError is consistent across all tools
