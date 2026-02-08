# Handoff: Extraction Reliability — v3.1 Plan Complete, Ready for Agent Assignment

Updated: 2026-02-08

## Completed

- **Pipeline review** — traced full extraction pipeline, found 3 critical gaps, 4 important issues
  - `docs/pipeline_review_extraction_reliability.md`

- **Content Quality Audit** — statistical analysis of 11,582 real pages
  - 45% have nav junk in first 2000 chars; median content start: 1,708 chars
  - Firecrawl strips `<header>/<nav>/<footer>` but NOT `<div>`-based nav

- **Phase 1E Robustness Validation** — tested 3 cleaning approaches on real data
  - Regex-only: 55.6% still dirty (English-dependent, removes only 15% of junk)
  - Link-density-only: 35.4% still dirty
  - **Combined (v3.1)**: usable embedding windows 40.6% → 66.5%, false positive 0.07%
  - True gap: 1.0% of pages have content cleaning misses (buried >6000 chars)
  - 15.2% genuinely link-only (no content anywhere on page — correct behavior)

- **Plan rewrite** — 1097 → 602 lines. Same code blocks, removed history/bloat
  - `docs/TODO_extraction_reliability.md` — complete implementation spec

## In Progress

Nothing — plan is final, awaiting agent assignment.

## Next Steps

- [ ] Commit all uncommitted files (4 files: HANDOFF.md, schema_extractor.py, 2 docs)
- [ ] Create `docs/TODO-agent-A.md` — Phase 0 + Phase 1 spec
- [ ] Create `docs/TODO-agent-B.md` — Phase 2 + Phase 3 spec
- [ ] `/assign-agent` for both agents
- [ ] Agents execute in parallel, review PRs, re-extract DBS to verify

## Key Files

- `docs/TODO_extraction_reliability.md` — **Master spec (v3.1)**, all phases with code
- `docs/pipeline_review_extraction_reliability.md` — Pipeline review evidence
- Uncommitted: `HANDOFF.md`, `src/services/extraction/schema_extractor.py` (minor boolean prompt tweak)
- Both docs are untracked — **commit before agents pull**

## Context

- **Agent A** (Phase 0+1): `config.py`, `embedding.py`, `smart_classifier.py`, `content_cleaner.py` (new), `client.py`
- **Agent B** (Phase 2+3): `schema_extractor.py`, `schema_orchestrator.py`, `smart_merge.py`
- No file conflicts between agents — safe to run in parallel
- **bge-m3 already deployed** on 192.168.0.136:9003 — same 1024 dims, drop-in replacement
- DBS test project: `b0cd5830-92b0-4e5e-be07-1e16598e6b78`
- Main batch (278 companies, 11,582 pages): `99a19141-9268-40a8-bc9e-ad1fa12243da`
