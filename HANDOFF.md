# Handoff: Extraction Pipeline Optimization Planning

## Completed
- Reviewed `TODO_extraction_optimization.md` against actual codebase
- Identified gaps between plan and implementation (return type changes, call chain, test files)
- Rewrote plan to focus on Phase 1 only (page classification)
- Deferred Phases 2-4 with clear reasoning
- Added risk analysis with breaking vs additive changes
- Designed 3-increment development approach with effort estimates
- Added two-stage feature flag rollout (`classification_enabled`, `classification_skip_enabled`)
- Identified all 6 test files needing updates for return type change

## In Progress
- `docs/TODO_extraction_optimization.md` is modified but not committed

## Next Steps
- [ ] Commit the updated TODO file
- [ ] Assign Increment 1 (Foundation) to an agent - ~2h, additive, no risk
- [ ] Assign Increment 2 (Classifier) to an agent in parallel - ~3h, additive, no risk
- [ ] After both merge, assign Increment 3 (Integration) - ~4h, has breaking changes

## Key Files
- `docs/TODO_extraction_optimization.md` - Complete implementation plan with code snippets
- `src/services/extraction/schema_orchestrator.py:29` - `extract_all_groups()` signature to change
- `src/services/extraction/pipeline.py:529` - Only caller of `extract_all_groups()`

## Context
**Breaking change in Increment 3:** Return type changes from `list[dict]` to `tuple[list[dict], ClassificationResult | None]`. Six test files must be updated in the same PR:
- `tests/test_pipeline_context.py`
- `tests/test_schema_orchestrator.py`
- `tests/test_parallel_extraction.py`
- `tests/test_schema_orchestrator_concurrency.py`
- `tests/test_extraction_pipeline.py`
- `tests/test_template_compatibility.py`

**Safe rollout:** Both feature flags default to `False`. Deploy all code first, then enable `classification_enabled` (filtering only), then `classification_skip_enabled` (full skip behavior).
