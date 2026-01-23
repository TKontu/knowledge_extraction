# Handoff: Crawl Delay Unit Mismatch Fix

**Session Date**: 2026-01-23
**Branch**: fix/crawl-delay-unit-mismatch
**Previous Commit**: 99be6cf - feat: Add debug logging to trace Firecrawl sitemap hangs (#51)

## Root Cause Identified and Fixed

### The Problem
Crawl jobs were hanging for ~33 minutes between page scrapes. The "Job done" log appeared, but jobs got stuck in `concurrentJobDone()` at the "Starting crawler delay sleep" step.

### Root Cause: Unit Mismatch in Delay Parameter

**Python pipeline** (`src/services/scraper/client.py`):
```python
# BEFORE (bug):
crawl_request["delay"] = delay_ms  # Sends 2000 (meaning 2000ms)

# AFTER (fix):
crawl_request["delay"] = delay_ms / 1000  # Sends 2 (meaning 2 seconds)
```

**Firecrawl** (`concurrency-limit.ts`):
```typescript
const delayMs = sc.crawlerOptions.delay * 1000;  // Multiplies by 1000
```

**Result of bug**:
- Python sends `delay=2000` (intending 2000ms = 2 seconds)
- Firecrawl interprets as 2000 seconds, multiplies: `2000 * 1000 = 2,000,000ms`
- **Actual delay: 33.33 minutes per job!**

### Evidence
Logs from rempco.com crawl:
- 14:01:46 - "Starting crawler delay sleep"
- 14:35:07 - Next page starts processing
- **Elapsed: 33 minutes 21 seconds** (matches 2,000,000ms exactly)

### Why scrapethissite.com Worked
The delay sleep only triggers when **promoting the next job from a backlog**. Scrapethissite completed fast enough that jobs didn't need promotion from backlog, so the delay code path was never executed.

## Fix Applied

**File**: `src/services/scraper/client.py` (lines 343-346)

```python
# Add rate limiting parameters if specified
# NOTE: Firecrawl expects delay in SECONDS, not milliseconds
# It multiplies by 1000 internally, so we must convert ms -> seconds
if delay_ms is not None:
    crawl_request["delay"] = delay_ms / 1000  # Convert ms to seconds
```

## Files Changed This PR
- `src/services/scraper/client.py` - Fix delay unit conversion
- `Dockerfile` - Cache bust for fresh build
- `HANDOFF.md` - Documentation

## To Deploy
```bash
./build-and-push.sh
# Then redeploy stack on remote
```

## Environment
- Remote: 192.168.0.136
- API: http://192.168.0.136:8742
