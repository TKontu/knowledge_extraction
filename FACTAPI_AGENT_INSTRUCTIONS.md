# factAPI — Agent Integration Guide

> Drop this file into any project so an AI agent knows how to connect to and query factAPI.

## Connection

- **Base URL**: `http://192.168.0.136:8484` (Docker)
- **Auth**: Pass `X-API-Key` header on read endpoints, `X-Admin-Key` on admin endpoints
- **Content-Type**: All responses are `application/json`
- **OpenAPI spec**: Available at `{BASE_URL}/openapi.json`
- **Swagger UI**: Available at `{BASE_URL}/docs`

## Read Endpoints

All require `X-API-Key` header (unless auth is disabled).

### List collections

```
GET /api/v1/collections
```

Returns `{ "collections": [{ "name", "columns", "row_count", "created_at", "updated_at" }] }`.

### Query a collection

```
GET /api/v1/collections/{name}
```

**Query parameters:**

| Param | Example | Effect |
|-------|---------|--------|
| `column=value` | `country=Japan` | Exact match filter |
| `column__gt` | `population__gt=1000000` | Greater than |
| `column__lt` | `price__lt=50` | Less than |
| `column__gte` | `age__gte=18` | Greater than or equal |
| `column__lte` | `rating__lte=3` | Less than or equal |
| `column__like` | `name__like=%smith%` | SQL LIKE pattern |
| `column__in` | `status__in=active,pending` | Match any in comma-separated list |
| `json_col.path` | `metadata.color=red` | Dot-notation filter on JSON columns |
| `_sort` | `_sort=-population` | Sort (prefix `-` for descending) |
| `_limit` | `_limit=25` | Max rows to return (default: 100, max: 1000) |
| `_offset` | `_offset=50` | Skip N rows |
| `_fields` | `_fields=name,price` | Return only these columns |
| `_search` | `_search=tokyo` | Full-text search across all columns |

**Response shape:**

```json
{
  "collection": "cities",
  "total": 47868,
  "count": 10,
  "limit": 100,
  "offset": 0,
  "data": [{ "city": "Tokyo", "country": "Japan", "population": 37785000 }]
}
```

### Get collection schema

```
GET /api/v1/collections/{name}/schema
```

Returns `{ "name": "cities", "columns": { "city": "TEXT", "population": "INTEGER" } }`.

Use this to discover available columns and their types before building queries.

### Get single record

```
GET /api/v1/collections/{name}/{record_id}
```

Returns a single row object by its `_id`.

## Admin Endpoints

All require `X-Admin-Key` header.

### Create collection (upload CSV)

```
POST /api/v1/admin/collections
Content-Type: multipart/form-data

name=<collection_name>
file=@<path_to_csv>
```

### Replace collection

```
PUT /api/v1/admin/collections/{name}
Content-Type: multipart/form-data

file=@<path_to_csv>
```

### Append rows

```
POST /api/v1/admin/collections/{name}/append
Content-Type: multipart/form-data

file=@<path_to_csv>
```

CSV headers must match existing columns exactly.

### Delete collection

```
DELETE /api/v1/admin/collections/{name}
```

Returns 204 No Content.

## Health Check

```
GET /health
```

Returns `{ "status": "healthy", "version": "0.1.0" }`.

## Error Responses

All errors follow this shape:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Collection 'foo' not found"
  }
}
```

| Status | Code | When |
|--------|------|------|
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 404 | `NOT_FOUND` | Collection or record doesn't exist |
| 409 | `CONFLICT` | Collection already exists (on create) |
| 422 | `VALIDATION_ERROR` | Invalid query params, bad CSV, or name validation failure |
| 500 | `INTERNAL_ERROR` | Unhandled server error |

## Python Example

```python
import httpx

BASE_URL = "http://localhost:8484"
HEADERS = {"X-API-Key": "your-api-key"}

# List collections
collections = httpx.get(f"{BASE_URL}/api/v1/collections", headers=HEADERS).json()

# Discover schema
schema = httpx.get(f"{BASE_URL}/api/v1/collections/cities/schema", headers=HEADERS).json()

# Query with filters
resp = httpx.get(
    f"{BASE_URL}/api/v1/collections/cities",
    params={"country": "Japan", "_sort": "-population", "_limit": "5"},
    headers=HEADERS,
).json()

for row in resp["data"]:
    print(row["city"], row["population"])
```

## curl Examples

```bash
# List collections
curl http://localhost:8484/api/v1/collections -H "X-API-Key: $KEY"

# Query with filters and sort
curl "http://localhost:8484/api/v1/collections/cities?country=Japan&_sort=-population&_limit=5" \
  -H "X-API-Key: $KEY"

# Search across all columns
curl "http://localhost:8484/api/v1/collections/cities?_search=tokyo" \
  -H "X-API-Key: $KEY"

# JSON dot-notation filter
curl "http://localhost:8484/api/v1/collections/products?metadata.color=red&_fields=name,metadata.color" \
  -H "X-API-Key: $KEY"

# Upload a CSV
curl -X POST http://localhost:8484/api/v1/admin/collections \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -F "name=products" -F "file=@products.csv"
```

## MCP Server

factAPI also exposes an MCP server for direct AI assistant integration (no HTTP needed):

```json
{
  "mcpServers": {
    "factapi": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/factAPI"
    }
  }
}
```

Available tools: `list_collections`, `get_schema`, `query_collection`, `search_collection`, `get_record`.
