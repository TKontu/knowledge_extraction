# Handoff: Domain Boilerplate Deduplication — Planning Complete

Updated: 2026-02-26

## Completed

### Extraction Reliability (Phases 0-3) — COMMITTED
- All code committed in `12f8bbd` on `main` (and `feature/domain-boilerplate-dedup` branch)
- 158 tests passing across 7 test files
- Phase 1A (enable classification) still pending — flip 4 config booleans

### Domain Boilerplate Dedup — Investigation & Planning
- **Data analysis** on 12,069 real pages across ~249 domains:
  - 19.5% of all stored content is boilerplate (23.7 MB of 121.4 MB)
  - 101 domains (41%) have >10% boilerplate
  - Worst: wattdrive.com 91.3%, psjengineering.co.za 90.5%, bauergears.com 52.4%
  - Boilerplate = cookie banners, product carousels, footer legal, repeated sidebars
- **Full spec written**: `docs/TODO_domain_dedup.md` (v1.0)
- **Branch created**: `feature/domain-boilerplate-dedup` (clean, at same commit as main)
- Confirmed compatibility with extraction reliability plan (complementary layers, no conflicts)

## In Progress

Nothing — planning complete, implementation not yet started.

## Next Steps

### Extraction Reliability (finish first)
- [ ] **Phase 1A**: Flip 4 classification config booleans to True in `src/config.py`
- [ ] Re-extract David Brown Santasalo to validate improvements
- [ ] Verify: no "Santasalo" as city, HQ = "Jyväskylä, Finland"

### Domain Boilerplate Dedup (on `feature/domain-boilerplate-dedup` branch)
- [ ] **Phase A**: Core algorithm — `src/services/extraction/domain_dedup.py` + tests
- [ ] **Phase B**: Data model — ORM + Alembic migration (`cleaned_content` column + `domain_boilerplate` table)
- [ ] **Phase C**: Repository + service class
- [ ] **Phase D**: Config + API endpoint + MCP tool
- [ ] **Phase E**: Pipeline integration (2-line change, gated by `domain_dedup_enabled=False`)
- [ ] **Phase F**: Enable, run on real data, validate

## Key Files

| File | Purpose |
|------|---------|
| `docs/TODO_domain_dedup.md` | Full implementation spec (Phases A-F) |
| `docs/TODO_extraction_reliability.md` | Extraction reliability spec (v3.2) — Phases 0-3 done, 1A pending |
| `src/services/extraction/pipeline.py:536` | Integration point — `source.content` → `source.cleaned_content or source.content` |
| `src/orm_models.py:282-337` | Source model — add `cleaned_content` column |
| `src/services/extraction/content_cleaner.py` | Existing per-page cleaning (complementary to domain dedup) |

## Context

- **Branch**: `feature/domain-boilerplate-dedup` — currently identical to `main` (no changes yet)
- **Algorithm**: Block-level hashing (split on `\n\s*\n`, SHA-256, 70% threshold) — validated on real data
- **Key design**: Original `sources.content` never modified, new `cleaned_content` column stores deduped version
- **Feature flag**: `domain_dedup_enabled=False` default — zero pipeline impact until explicitly enabled
- **Triggered manually**: API/MCP tool to analyze domains, not automatic (can be automated later)
- **DBS project ID**: `b0cd5830-92b0-4e5e-be07-1e16598e6b78` (test), `99a19141-...` (main batch)
