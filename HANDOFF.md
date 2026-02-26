# Handoff: Domain Boilerplate Deduplication — Implementation Complete

Updated: 2026-02-26

## Completed

### Extraction Reliability (Phases 0-3) — COMMITTED
- All code committed in `12f8bbd` on `main`
- 158 tests passing across 7 test files
- Phase 1A (enable classification) still pending — flip 4 config booleans

### Domain Boilerplate Dedup (Phases A-E) — COMMITTED
Implemented on `feature/domain-boilerplate-dedup` branch in 3 commits:

| Commit | Phases | What |
|--------|--------|------|
| `1fd01e7` | A-C | Core algorithm, data model, repository, service |
| `da070b8` | D | Config, API endpoints, MCP tools, client |
| `2c34772` | E | Pipeline integration (gated by feature flag) |

**Phase A** — Core algorithm (`src/services/extraction/domain_dedup.py`):
- `split_into_blocks()`, `hash_block()`, `compute_domain_fingerprint()`, `strip_boilerplate()`
- 26 tests in `tests/test_domain_dedup.py`

**Phase B** — Data model:
- `DomainBoilerplate` ORM model in `src/orm_models.py`
- `cleaned_content` column on `Source`
- Alembic migration `a1b2c3d4e5f6`

**Phase C** — Repository + service:
- `DomainBoilerplateRepository` (upsert/get/list/delete)
- `SourceRepository.get_domains_for_project()` + `get_by_project_and_domain()`
- `DomainDedupService` (analyze_domain, analyze_project, get_domain_stats)

**Phase D** — Config + API + MCP:
- 4 config settings: `domain_dedup_enabled`, `threshold_pct`, `min_pages`, `min_block_chars`
- `POST /api/v1/projects/{id}/analyze-boilerplate` + `GET .../boilerplate-stats`
- MCP tools: `analyze_boilerplate`, `get_boilerplate_stats`
- Client methods in `src/ke_mcp/client.py`

**Phase E** — Pipeline integration:
- 2 locations in `pipeline.py` prefer `cleaned_content` when `domain_dedup_enabled=True`
- Gated by `domain_dedup_enabled=False` default — zero behavior change

## In Progress

Nothing — all implementation complete.

## Next Steps

### Domain Boilerplate Dedup — Phase F (Enable + Validate)
- [ ] Run Alembic migration: `alembic upgrade head`
- [ ] Run `analyze_boilerplate` on Industrial Drivetrain project (`99a19141-...`)
- [ ] Inspect stats — expect ~19.5% average content reduction
- [ ] Spot-check `cleaned_content` for bauergears.com, flender.com
- [ ] Set `domain_dedup_enabled=True` in config
- [ ] Re-extract a test domain (e.g., David Brown Santasalo) and compare quality
- [ ] Merge `feature/domain-boilerplate-dedup` → `main`

### Extraction Reliability — Phase 1A (pending)
- [ ] Flip 4 classification config booleans to True in `src/config.py`
- [ ] Re-extract David Brown Santasalo to validate
- [ ] Verify: no "Santasalo" as city, HQ = "Jyväskylä, Finland"

## Key Files

| File | Purpose |
|------|---------|
| `src/services/extraction/domain_dedup.py` | Core algorithm + DomainDedupService |
| `src/services/storage/repositories/domain_boilerplate.py` | DomainBoilerplate repository |
| `src/services/storage/repositories/source.py` | Added domain query helpers |
| `src/orm_models.py` | DomainBoilerplate model + Source.cleaned_content |
| `alembic/versions/20260226_add_domain_boilerplate.py` | Migration `a1b2c3d4e5f6` |
| `src/api/v1/dedup.py` | REST API endpoints |
| `src/ke_mcp/tools/dedup.py` | MCP tools |
| `src/ke_mcp/client.py` | Client methods (analyze_boilerplate, get_boilerplate_stats) |
| `src/config.py` | 4 domain_dedup_* settings |
| `src/services/extraction/pipeline.py` | Integration points (lines ~149, ~539) |
| `tests/test_domain_dedup.py` | 26 tests for core algorithm |
| `docs/TODO_domain_dedup.md` | Full implementation spec |

## Context

- **Branch**: `feature/domain-boilerplate-dedup` — 3 commits ahead of `main`
- **Algorithm**: Block-level SHA-256 hashing (whitespace-normalized, lowercased, 16 hex chars)
- **Threshold**: 70% of pages within a domain (configurable)
- **Key design**: Original `sources.content` never modified; `cleaned_content` is separate
- **Feature flag**: `domain_dedup_enabled=False` default — zero pipeline impact until enabled
- **Migration required**: `alembic upgrade head` before using
- **DBS project ID**: `b0cd5830-92b0-4e5e-be07-1e16598e6b78` (test), `99a19141-...` (main batch)
