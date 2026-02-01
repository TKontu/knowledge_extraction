# Handoff: Batch Crawl Running

Updated: 2026-02-01

## Completed This Session

- **Cleaned input file** - Fixed 278 URLs in `input/companies.txt`:
  - Fixed protocol typos (`nttps://` → `https://`)
  - Fixed domain typos (`wwww.` → `www.`)
  - Removed duplicates and invalid URLs
  - Organized by region (Global/NA, Latin America, Australia/NZ)

- **Updated batch crawl script** (`scripts/crawl_companies.py`):
  - Changed default depth from 3 to **5**
  - Disabled `prefer_english_only` (was filtering out Spanish/Portuguese content)
  - Made `focus_terms` optional (was too strict for filtering)
  - Added `--english-only` flag for opt-in language filtering
  - Uses `drivetrain_company_analysis` template

- **Created project**: `Industrial Drivetrain Companies 2026`
  - Project ID: `b0cd5830-92b0-4e5e-be07-1e16598e6b78`
  - Template: `drivetrain_company_analysis`

- **Started batch crawl** - 278 companies queued and processing

## In Progress

**Batch crawl running** - Jobs are in smart crawl scrape phase:
- All services healthy (pipeline, camoufox, firecrawl, qdrant, postgres)
- Scrapes completing with HTTP 200
- Multi-language content being captured (Spanish, Portuguese, Italian, French)
- Some expected failures: dead domains, RSS feed URLs

## Known Failures (Expected)

| URL | Error | Reason |
|-----|-------|--------|
| `iabactransmisiones.com.ar` | NS_ERROR_UNKNOWN_HOST | Domain dead/unresolvable |
| `*/feed` URLs | Download is starting | RSS feeds, not HTML pages |

## Next Steps

- [ ] Monitor crawl completion via Portainer logs or API
- [ ] Run extraction after crawls complete: `POST /api/v1/projects/{project_id}/extract`
- [ ] Review extracted data quality
- [ ] Address remaining TODO files (production readiness, database consistency)

## Key Files

| File | Purpose |
|------|---------|
| `input/companies.txt` | Cleaned URL list (278 companies) |
| `scripts/crawl_companies.py` | Batch crawl script with new defaults |
| `output/crawl_batch_state.json` | Resume state for batch script |

## Script Usage

```bash
# Check crawl status
curl "http://192.168.0.136:8742/api/v1/jobs?type=crawl&status=running" -H "X-API-Key: thisismyapikey3215215632"

# Resume if interrupted
source .venv/bin/activate && python scripts/crawl_companies.py --resume

# Monitor logs via Portainer MCP
mcp__portainer__dockerProxy (container logs)
```

## Configuration Changes

| Parameter | Old | New | Reason |
|-----------|-----|-----|--------|
| `max_depth` | 3 | 5 | Deeper crawl for more content |
| `prefer_english_only` | True | False | LLM can translate during extraction |
| `focus_terms` | hardcoded | None | Optional, was too strict |

## Estimated Completion

Smart crawl batch: **2-4 hours** for 278 companies
