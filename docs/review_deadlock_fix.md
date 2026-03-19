## RESOLVED — commit 24271a3 (2026-03-18)

Timeout check moved inside the `status == "scraping"` branch (correct placement). Batch scrape timeout implemented (30 min / 1800s). Unknown status handling implemented (marks job failed). Stale detection updated. Kept for historical reference.

---

# Pipeline Review: Deadlock Fix (scheduler.py + crawl_worker.py)

## Must fix

- [ ] **crawl_worker.py:787-804 — Timeout kills completed/failed jobs.** The 30-min timeout check runs *before* the status if/elif chain. If Firecrawl returns `status="completed"` (or `"failed"`) on a poll where `elapsed > 1800`, the timeout fires first, marks the job FAILED with "timed out", and returns — the completed branch is never reached. Scraped pages are lost. This will happen with large batch scrapes where map + filter + scrape time exceeds 30 minutes. Fix: move the timeout check inside the `status == "scraping"` branch (or add `and status.status not in ("completed", "failed")` guard).
