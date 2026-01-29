# Handoff: TODO Review and Cleanup

## Completed

- **Reviewed and updated TODO files**
  - `TODO_production_readiness.md`: Removed stale "Commit Pending Changes" item (already merged via PR #74)
  - `TODO_architecture_database_consistency.md`: Moved stale job threshold fix to Completed (was already implemented with per-job-type configurable thresholds)
  - Removed dead reference to deleted `TODO_extraction_reliability.md`
  - Renumbered items and updated review dates

- **Discovered completed work not tracked**
  - Stale job thresholds are now configurable per job type in `src/config.py:313-324`
  - Scrape: 5min, Extract: 15min, Crawl: 30min (was 5 seconds for all)

## In Progress

Nothing in progress - clean working directory.

## Next Steps

- [ ] **HIGH**: Add background task for orphaned extraction retry (`embedding_id IS NULL`)
- [ ] **HIGH**: Add alerting for partial-failure states (PostgreSQL success, Qdrant failure)
- [ ] **MEDIUM**: Fix async/sync mismatch in repositories (Option A: AsyncSession, or Option B: remove async keywords)
- [ ] **MEDIUM**: Replace LLM response polling with Redis pub/sub
- [ ] **LOW**: Add unit tests for `update_embedding_ids_batch()` and `_job_duration_by_type()`

## Key Files

- `docs/TODO_architecture_database_consistency.md` - 8 remaining items (1 HIGH, 3 MEDIUM, 4 LOW)
- `docs/TODO_production_readiness.md` - 8 remaining items (1 HIGH, 3 MEDIUM, 4 LOW)
- `src/config.py:313-324` - Per-job-type stale thresholds (already implemented)
- `src/services/scraper/scheduler.py:43-52` - `get_stale_thresholds()` function

## Context

- Two active TODO files track remaining technical debt
- No open PRs
- All previous agent work (PRs #63-74) has been merged
- System uses 3 databases (PostgreSQL, Redis, Qdrant) without distributed transactions - acceptable for eventual consistency use case
