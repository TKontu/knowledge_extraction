# Pipeline Review: POST /scrape (Camoufox Service)

## Flow
```
server.py:scrape_url → scraper.py:scrape → scraper.py:_do_scrape → Camoufox browser
```

## Critical (must fix)

- [ ] **scraper.py:74** - `AsyncCamoufox` instantiation may fail silently. The `geoip=True` parameter requires the `camoufox[geoip]` extra; if not installed, it may throw or silently disable geoip. Browser start errors are not caught, crashing the entire service startup.

- [ ] **models.py:45-59** - Missing `contentType` field in `ScrapeSuccessResponse`. Firecrawl's Playwright service returns `contentType` (line 275 of api.ts), but our model doesn't include it. This could break Firecrawl's content-type detection for JSON/plain-text responses.

- [ ] **scraper.py:170-174** - Using `wait_until="domcontentloaded"` differs from Firecrawl which uses `wait_until="load"` (api.ts:263). This may return content before all resources are loaded, potentially missing dynamically-loaded content.

## Important (should fix)

- [ ] **server.py:124** - No URL validation before scraping. Firecrawl validates URLs (api.ts:234-239) and returns 400 for invalid URLs. Our implementation passes any string directly to browser, which will fail with an unclear error.

- [ ] **scraper.py:159-160** - Headers applied to context, not page. Firecrawl applies headers via `page.setExtraHTTPHeaders()` (api.ts:259-261), we use `context.extra_http_headers`. This may have different behavior for navigation vs sub-resource requests.

- [ ] **scraper.py:188-196** - Selector check continues on failure with warning. Firecrawl throws "Required selector not found" (api.ts:175-176) and returns 500. Our behavior silently continues, which could mask content loading failures.

- [ ] **scraper.py:202** - Default status code 200 when response is None. If navigation fails but no exception is raised, we return 200 with whatever partial content exists. Should be an error condition.

- [ ] **scraper.py:74** - No ad-blocking routes. Firecrawl blocks 13 ad-serving domains (api.ts:61-75) and optionally blocks media files (api.ts:127-131). Missing this could slow page loads and leak requests to ad networks.

## Minor

- [ ] **config.py:29-33** - `alias="pool_size"` creates confusion. Environment variable is `CAMOUFOX_POOL_SIZE` but field is `max_concurrent_pages`. Documentation and docker-compose use `POOL_SIZE` but the property used internally is different.

- [ ] **scraper.py:178** - Hardcoded 10000ms networkidle timeout. Should use a configurable timeout or derive from request timeout.

- [ ] **server.py:13** - Unused import `HTTPException`. Imported but never used.

- [ ] **scraper.py:179-181** - Silent exception swallowing for network idle. No log level specified, defaults to debug which may be filtered in production.

- [ ] **scraper.py:184-196** - JSON/plain-text body extraction missing. Firecrawl extracts raw body for `application/json` or `text/plain` content types (api.ts:184-186). Our implementation always returns DOM HTML, which may wrap JSON in HTML elements.
