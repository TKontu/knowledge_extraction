# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-09

## Current State

### Deployed & Running
- **v2 extraction pipeline** live with three-tier grounding gate, LLM quote rescue, negation filtering, confidence recalibration
- **Full re-extraction in progress** (all three projects, `force=True`):

| Project | Job ID | Sources | Status |
|---------|--------|---------|--------|
| Industrial Drivetrain | `66c9b300-3f9e-4dce-b13b-b39e1e549b4b` | 11,340 | Running |
| Jobs Quoting Trial | `60d84d11-7ccb-4a65-934d-6dcffc7a6867` | 35 | Queued |
| Wikipedia Quoting Trial | `e4dfc752-b58e-4be5-8dab-bb76cd0adc2d` | 20 | Queued |

### Observed During Extraction
- **Grounding gate active**: `v2_source_grounding_retry` events firing when `avg_grounding` is low (0.0, 0.35, 0.4)
- **Truncation issue on `company_meta`**: Some Brazilian company pages contain municipality lists; the LLM treats them as manufacturing locations, generating 28K+ char responses that exceed 8192 max_tokens. JSON truncation → repair fails → retries exhaust. These sources end up with missing/partial `company_meta` data. Not blocking.
- **Root cause**: World-knowledge leakage (8.7% of extractions) — the prompt anti-hallucination fix (Phase B, Step 6) would mitigate this.

## Completed (all deployed)

- **Three-tier grounding gate** (Phase A, `docs/TODO_extraction_quality.md`):
  - `is_negation_quote()` in `grounding.py` — regex filter for "No mention of..." / "N/A" quotes
  - `rescue_quote()` in `llm_grounding.py` — LLM finds exact verbatim passage, re-verifies
  - `apply_grounding_gate()` in `schema_orchestrator.py` — >=0.8 KEEP, 0.3-0.8 LLM RESCUE, <0.3 DROP
  - `effective_weight() = min(confidence, grounding_score)` in `consolidation.py`
- **Pipeline review & 5 fixes** (dead code removal, semaphore optimization, entity rescue)
- **v2 extraction pipeline** — per-field structured data, inline grounding, cardinality-based merge, entity pagination
- **Grounding & consolidation pipeline** — 6 increments, 164 tests, +4654 lines
- **Domain boilerplate dedup** — all phases (A-F)
- **DB migrations** — `grounding_scores` JSONB, `consolidated_extractions`, `data_version`
- **All prior fixes** — pipeline fixes, extraction reliability, scheduler resilience, typed config facades

## Next Steps (prioritized)

### 1. Post-extraction analysis (after current jobs complete)
- [ ] Compare grounding metrics before/after for drivetrain — quantify improvement from grounding gate
- [ ] Check quality on jobs and wikipedia — confirm high grounding rates
- [ ] Run consolidation: `POST /projects/{id}/consolidate` on all three projects

### 2. Prompt improvements (Phase B) — A/B TRIAL COMPLETE, READY TO DEPLOY
- [x] **A/B trial** — `scripts/trial_prompt_ab.py`, 30 sources × 7 groups, paired comparison
  - Well grounded: 88.3% → 90.8% (+2.5pp), poorly grounded: 6.4% → 5.1% (-1.3pp)
  - Value-as-quote: 2.1% → 0.5% (4x reduction), 0 regressions, 3 fields fixed
  - 0.5s faster per call, 7 more fields extracted
- [ ] **Deploy anti-hallucination prompt** — hallucination guard block + quote-not-value instruction in `schema_extractor.py`
- [ ] **Re-extract after deploy** to confirm at-scale improvement
- **Known artifact**: `company_name` quoting from user prompt context line — separate issue, not hallucination

### 3. Position tracing (Phase C)
- [ ] **Implement quote-to-source tracing** — `docs/TODO_quote_source_tracing.md`. Algorithm validated: 87.3% match rate. Reference impl: `scripts/trial_ground_and_locate.py`

### 4. Classification (Phase C)
- [ ] **LLM skip-gate** — binary with gemma3-4B, 92.6% recall. See `docs/TODO_classification_robustness.md`

### 5. Later
- [ ] Report integration with consolidation
- [ ] Search fix + reranking (bge-reranker-v2-m3)
- [ ] Multilingual product dedup during consolidation
- [ ] Field-specific grounding thresholds

## Deployment Context

- **Remote server**: `192.168.0.136`
- **Deployed via**: `docker-compose.prod.yml` (builds from GitHub `main` branch)
- **Pipeline API**: `http://192.168.0.136:8742` (container port 8000 -> host 8742)
- **LLM**: vLLM on `192.168.0.247:9003` (gemma3-12b-awq default, Qwen3-30B for verification)
- **Embeddings**: bge-m3 on `192.168.0.136:9003`
- **DB**: `scristill:scristill@192.168.0.136:5432/scristill` (psycopg v3)
- **Portainer env ID**: 3
- **DB column**: `uri` not `url` on sources table

## Key Files

- `src/services/extraction/grounding.py` — `verify_quote_in_source()` (3-tier), `ground_field_item()`, `is_negation_quote()`
- `src/services/extraction/llm_grounding.py` — `LLMGroundingVerifier`, `rescue_quote()`
- `src/services/extraction/schema_orchestrator.py` — `apply_grounding_gate()`, `_parse_chunk_to_v2()`, `_extract_entity_chunk_v2()`
- `src/services/extraction/consolidation.py` — 6 strategies, `effective_weight()`
- `src/services/extraction/schema_extractor.py` — extraction prompts (Phase B target)
- `src/services/llm/chunking.py` — `chunk_document()`
- `docker-compose.prod.yml` — `EXTRACTION_DATA_VERSION=2`

## Project IDs

- **Drivetrain**: `99a19141-9268-40a8-bc9e-ad1fa12243da` (11,340 sources + 729 skipped)
- **Jobs trial**: `b972e016-3baa-403f-ae79-22310e4e895a` (35 sources)
- **Wikipedia trial**: `6ce9755e-9d77-4926-90dd-86d4cd2b9cda` (20 sources)

## TODO Docs Status

| Doc | Status |
|-----|--------|
| `docs/TODO_grounding_and_consolidation.md` | COMPLETE — all 6 increments |
| `docs/TODO_extraction_quality.md` | Phase A COMPLETE & deployed, Phase B pending |
| `docs/TODO_grounded_extraction.md` | Layers 1+3 COMPLETE, Layer 2 (skip-gate) + Layers 4-5 pending |
| `docs/TODO_classification_robustness.md` | Ready to implement (v3 spec) |
| `docs/TODO_quote_source_tracing.md` | Ready to implement (algorithm validated) |
| `docs/TODO_global_sources.md` | Ready to implement |

## Context

- **v2 is live**: All new extractions use v2 format with inline grounding. v1 data coexists.
- **Grounding gate deployed**: Three-tier gate drops fabricated data, rescues borderline paraphrases.
- **Test suite**: ~2055 tests passing
- **Content is NOT sentences**: Tables, lists, key-value specs, fragments. Block-based matching (`\n\n` then `\n`) aligns with actual content structure.
