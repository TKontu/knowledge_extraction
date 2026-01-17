# TODO: Firecrawl AJAX Discovery Integration

**Agent ID**: `firecrawl-ajax`
**Branch**: `feature/ajax-discovery`
**Priority**: High

---

## Context

We have a Camoufox browser service that can discover AJAX/JavaScript-loaded URLs by clicking interactive elements and capturing XHR/fetch requests. This is essential for crawling pages where content/links are loaded dynamically (e.g., tabs that load data via AJAX).

**Current State**:
- Camoufox service accepts `discover_ajax: true` in scrape requests
- Camoufox returns `discoveredUrls: string[]` in response when AJAX URLs are found
- Firecrawl's playwright engine does NOT pass `discover_ajax` option
- Firecrawl's playwright engine does NOT consume `discoveredUrls` response
- Firecrawl's link discovery only extracts `<a href>` from HTML, missing AJAX-loaded URLs

**Test Case**: `https://www.scrapethissite.com/pages/ajax-javascript/`
- Page has year tabs (2010-2015) that load Oscar film data via AJAX
- Standard link extraction finds 0 crawlable links
- With AJAX discovery: finds 6 URLs like `?ajax=true&year=2015`

---

## Objective

Modify Firecrawl's playwright engine to support AJAX URL discovery, enabling crawls to follow JavaScript-loaded links.

---

## Tasks

### Task 1: Update Playwright Engine Request

**File**: `apps/api/src/scraper/scrapeURL/engines/playwright/index.ts`

Add `discover_ajax: true` to the request body sent to the playwright/camoufox service.

**Current code (lines 16-22)**:
```typescript
body: {
  url: meta.rewrittenUrl ?? meta.url,
  wait_after_load: meta.options.waitFor,
  timeout: meta.abort.scrapeTimeout(),
  headers: meta.options.headers,
  skip_tls_verification: meta.options.skipTlsVerification,
},
```

**Change to**:
```typescript
body: {
  url: meta.rewrittenUrl ?? meta.url,
  wait_after_load: meta.options.waitFor,
  timeout: meta.abort.scrapeTimeout(),
  headers: meta.options.headers,
  skip_tls_verification: meta.options.skipTlsVerification,
  discover_ajax: meta.options.discoverAjax ?? false,
},
```

### Task 2: Update Playwright Engine Response Schema

**File**: `apps/api/src/scraper/scrapeURL/engines/playwright/index.ts`

Add `discoveredUrls` to the Zod schema (lines 25-30).

**Current code**:
```typescript
schema: z.object({
  content: z.string(),
  pageStatusCode: z.number(),
  pageError: z.string().optional(),
  contentType: z.string().optional(),
}),
```

**Change to**:
```typescript
schema: z.object({
  content: z.string(),
  pageStatusCode: z.number(),
  pageError: z.string().optional(),
  contentType: z.string().optional(),
  discoveredUrls: z.array(z.string()).optional(),
}),
```

### Task 3: Return Discovered URLs in Engine Result

**File**: `apps/api/src/scraper/scrapeURL/engines/playwright/index.ts`

Add `discoveredUrls` to the returned `EngineScrapeResult` (around line 39-47).

**Current code**:
```typescript
return {
  url: meta.rewrittenUrl ?? meta.url,
  html: response.content,
  statusCode: response.pageStatusCode,
  error: response.pageError,
  contentType: response.contentType,
  proxyUsed: "basic",
};
```

**Change to**:
```typescript
return {
  url: meta.rewrittenUrl ?? meta.url,
  html: response.content,
  statusCode: response.pageStatusCode,
  error: response.pageError,
  contentType: response.contentType,
  proxyUsed: "basic",
  discoveredUrls: response.discoveredUrls,
};
```

### Task 4: Update EngineScrapeResult Type

**File**: `apps/api/src/scraper/scrapeURL/engines/index.ts`

Add `discoveredUrls` to the `EngineScrapeResult` type definition.

Find the type definition and add:
```typescript
discoveredUrls?: string[];
```

### Task 5: Add discoverAjax to Scrape Options

**File**: Find the options/meta type definition (likely in `apps/api/src/scraper/scrapeURL/index.ts` or a types file)

Add `discoverAjax?: boolean` to the scrape options type so it can be passed through the API.

### Task 6: Merge Discovered URLs with Extracted Links

**File**: `apps/api/src/scraper/scrapeURL/transformers/index.ts` (or wherever links are aggregated)

Find where `extractLinks()` is called and merge `discoveredUrls` into the result.

**Pseudocode**:
```typescript
// After extracting links from HTML
const htmlLinks = await extractLinks(html, baseUrl);

// Merge with AJAX-discovered URLs (if any)
const allLinks = [...htmlLinks];
if (engineResult.discoveredUrls?.length) {
  allLinks.push(...engineResult.discoveredUrls);
}

// Deduplicate
const uniqueLinks = [...new Set(allLinks)];
```

---

## Test Plan

### Unit Test
Create a test in `apps/api/src/scraper/scrapeURL/engines/playwright/__tests__/` that mocks the playwright service response with `discoveredUrls` and verifies they're returned.

### Integration Test
1. Start the orchestrator stack with Camoufox service
2. Call the scrape endpoint with AJAX test page:
```bash
curl -X POST http://localhost:3002/v1/scrape \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer fc-test" \
  -d '{
    "url": "https://www.scrapethissite.com/pages/ajax-javascript/",
    "discoverAjax": true
  }'
```
3. Verify response includes discovered AJAX URLs

### Crawl Test
```bash
curl -X POST http://localhost:3002/v1/crawl \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer fc-test" \
  -d '{
    "url": "https://www.scrapethissite.com/pages/ajax-javascript/",
    "limit": 10,
    "maxDepth": 2
  }'
```
Verify crawl discovers and follows the 6 year URLs.

---

## Constraints

- Do NOT modify files outside the `apps/api/` directory
- Do NOT change the default behavior - `discover_ajax` should default to `false`
- Do NOT add new dependencies
- Maintain TypeScript type safety
- Follow existing code style (check with `pnpm lint`)

---

## Files to Modify (Summary)

| File | Change |
|------|--------|
| `apps/api/src/scraper/scrapeURL/engines/playwright/index.ts` | Add request param, schema field, return field |
| `apps/api/src/scraper/scrapeURL/engines/index.ts` | Add to EngineScrapeResult type |
| `apps/api/src/scraper/scrapeURL/index.ts` (or types file) | Add discoverAjax option |
| `apps/api/src/scraper/scrapeURL/transformers/index.ts` | Merge discovered URLs |

---

## Verification

- [ ] `pnpm lint` passes
- [ ] `pnpm build` succeeds
- [ ] Scrape with `discoverAjax: true` returns `discoveredUrls` in response
- [ ] Crawl on AJAX page discovers and follows dynamically-loaded URLs
- [ ] Default behavior unchanged (no AJAX discovery when option not set)

---

## PR Instructions

Create PR to `TKontu/firecrawl` repository:
- Branch: `feature/ajax-discovery`
- Title: `feat: Add AJAX URL discovery support for playwright engine`
- Base: `main`
