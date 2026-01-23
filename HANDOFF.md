# Handoff: Granular Post-Scrape Logging Implementation

**Session Date**: 2026-01-23
**Branch**: main
**Previous Commit**: 99be6cf - feat: Add debug logging to trace Firecrawl sitemap hangs (#51)

## Completed This Session

### Implemented Granular Post-Scrape Phase Logging

Based on the gap analysis from the previous session (where "Job done" logged but the job hung for 5+ minutes), I traced the exact code path and added detailed logging to identify WHERE the hang occurs.

**Root Cause Analysis**:
The "Job done" log at line 577 in `processJob()` is misleading - it logs BEFORE the actual job completion. The full post-scrape code path is:

1. `processJob()` → logs "Job done" → returns
2. `processJobInternal()` cleanup:
   - `deleteJobPriority()` - simple Redis SREM
   - **`concurrentJobDone()`** - **MOST LIKELY CULPRIT**:
     - Multiple Redis calls to remove from concurrency limits
     - Loop (up to 10 iterations) to promote next jobs
     - Calls `getACUCTeam()` (database/cache call) per iteration
     - Calls `getNextConcurrentJob()` which does Redis zscan/zrem
     - **Can SLEEP** if crawler delay is configured (line 342-344)
     - Calls `promoteJobFromBacklogOrAdd()` (PostgreSQL)
3. `nuq-worker.ts`: `scrapeQueue.jobFinish()` - PostgreSQL update + RabbitMQ notification

**Files Modified**:

1. **`vendor/firecrawl/apps/api/src/lib/concurrency-limit.ts`**
   - Added detailed timing to `concurrentJobDone()`:
     - ENTRY/EXIT logs with total duration
     - Per-step timing for each Redis operation
     - Loop iteration logging with durations
     - Crawler delay sleep logging (if triggered)
     - Promotion result logging

2. **`vendor/firecrawl/apps/api/src/services/worker/scrape-worker.ts`**
   - Added timing to `processJobWithTracing()`:
     - ENTRY log with mode and skipNuq flag
     - Timing for `addJobPriority()`
     - Timing for each job type processing
     - Inner/outer finally block logging
     - Timing around `concurrentJobDone()` call

3. **`vendor/firecrawl/apps/api/src/services/worker/nuq-worker.ts`**
   - Added logging around job completion:
     - Timing for `jobFinish()` and `jobFail()` calls
     - Total elapsed time tracking
     - "Job fully completed" final log

4. **`vendor/firecrawl/apps/api/src/services/worker/nuq.ts`**
   - Added step-by-step logging to `jobFinish()` and `jobFail()`:
     - PostgreSQL UPDATE timing
     - pg_notify timing (if using Postgres listener)
     - RabbitMQ sendJobEnd timing (if using RabbitMQ)

### Camoufox Not Needed

**Decision**: Camoufox does NOT need additional logging for this issue.

**Reasoning**: Camoufox is only involved during the actual scraping phase (via Playwright). Since we see "Job done" log successfully, the scrape completed. The hang is entirely in post-processing which is all TypeScript/Redis/PostgreSQL operations - no browser involvement.

## Expected Log Output After Deployment

With debug logging enabled, a job should now produce logs like:
```
processJobWithTracing ENTRY {mode: "single_urls", skipNuq: false}
addJobPriority completed {durationMs: 2}
processJob completed {durationMs: 3200, success: true}
Set most-recent-success in Redis {durationMs: 1}
Starting job cleanup (inner finally) {elapsedSinceEntry: 3205}
deleteJobPriority completed {durationMs: 1}
Inner finally cleanup completed {cleanupDurationMs: 3}
Starting outer finally block {skipNuq: false, elapsedSinceEntry: 3210}
Starting concurrentJobDone {elapsedSinceEntry: 3210}
concurrentJobDone ENTRY {...}
removeConcurrencyLimitActiveJob completed {durationMs: 2}
cleanOldConcurrencyLimitEntries completed {durationMs: 1}
... (more detailed concurrency operations)
concurrentJobDone EXIT {totalDurationMs: 150}
concurrentJobDone completed {durationMs: 150, elapsedSinceEntry: 3360}
Outer finally block completed {totalElapsedMs: 3365}
Job processing completed {success: true, durationMs: 3365}
Starting jobFinish call {elapsedSinceJobStart: 3370}
jobFinish: Starting PostgreSQL update {...}
jobFinish: PostgreSQL update completed {durationMs: 5}
jobFinish completed {success: true, durationMs: 8}
Job fully completed {totalElapsedMs: 3380}
```

If a hang occurs, we'll see exactly which step is blocking (e.g., "Starting concurrentJobDone" with no "EXIT" log, or stuck in a specific iteration).

## Next Steps

1. **Build and deploy** the new firecrawl-api image with granular logging
2. **Test with rempco.com** (or similar hanging URL) to capture exact hang location
3. **Analyze logs** to determine:
   - Is it `concurrentJobDone()` hanging?
   - Is it a specific Redis operation?
   - Is it the crawler delay sleep being triggered?
   - Is it `promoteJobFromBacklogOrAdd()` (PostgreSQL)?
   - Is it `jobFinish()` (PostgreSQL/RabbitMQ)?
4. **Implement fix** once root cause is identified

## Key Files

### Modified This Session
- `vendor/firecrawl/apps/api/src/lib/concurrency-limit.ts` - Granular logging for concurrentJobDone
- `vendor/firecrawl/apps/api/src/services/worker/scrape-worker.ts` - processJobWithTracing timing
- `vendor/firecrawl/apps/api/src/services/worker/nuq-worker.ts` - jobFinish/jobFail timing
- `vendor/firecrawl/apps/api/src/services/worker/nuq.ts` - PostgreSQL/RabbitMQ step logging

### Previously Modified (PR #51)
- `vendor/firecrawl/apps/api/src/services/worker/nuq-worker.ts` - Lock renewal stale warnings
- `vendor/firecrawl/apps/api/src/services/worker/scrape-worker.ts` - Job processing entry/exit logs
- `vendor/firecrawl/apps/api/src/scraper/crawler/sitemap.ts` - Heartbeat logging
- `src/services/scraper/crawl_worker.py` - Stale crawl warnings
- `src/services/scraper/client.py` - API timing

## Context

### Suspected Hang Locations (in order of likelihood)
1. **`concurrentJobDone()`** - Complex function with multiple async operations and potential delays
2. **`getACUCTeam()`** - Database call inside concurrentJobDone loop
3. **`promoteJobFromBacklogOrAdd()`** - PostgreSQL operation
4. **`jobFinish()`** - PostgreSQL update + notification

### Environment
- Remote: 192.168.0.136
- API: http://192.168.0.136:8742
- API Key: thisismyapikey3215215632

### Test Projects
- **Debug Test**: 3950b8f8-879b-4597-8c69-0c5dbe185939 (scrapethissite - worked)
- **Rempco Test**: dcd42f23-c7c4-42f8-9a90-2e25a22817e9 (rempco.com - hung)

---

**Status**: Granular post-scrape logging implemented. Ready for build and deployment to capture exact hang location.

**To Deploy**:
```bash
# From project root
./build-and-push.sh
# Then redeploy stack on remote
```
