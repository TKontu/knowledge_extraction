# Pipeline Review: Domain Boilerplate Dedup + Extraction Reliability

**Date:** 2026-02-26
**Scope:** All changes on `feature/domain-boilerplate-dedup` (now merged) + extraction reliability commits
**Branch:** `main` @ `c59754a`

## Flow

### Domain Dedup Pipeline
```
API dedup.py:analyze_boilerplate
  → DomainDedupService.analyze_project()
    → SourceRepository.get_domains_for_project()
    → per domain: DomainDedupService.analyze_domain()
      → SourceRepository.get_by_project_and_domain()
      → compute_domain_fingerprint() (pure)
      → strip_boilerplate() (pure)
      → source.cleaned_content = cleaned
      → DomainBoilerplateRepository.upsert()
      → session.flush()
    → session.commit()
```

### Extraction Pipeline (integration points)
```
pipeline.py:ExtractionPipelineService.process_source() [line ~149]
  → content = (source.cleaned_content or source.content) if domain_dedup_enabled else source.content

pipeline.py:SchemaExtractionPipeline.extract_source() [line ~539]
  → dedup_content = (source.cleaned_content or source.content) if domain_dedup_enabled else source.content
```

---

## Critical (must fix)

_(none found)_

## Important (should fix)

- [ ] **src/services/extraction/pipeline.py:149,539** — `cleaned_content or content` uses truthiness, not `is not None`. If `strip_boilerplate()` removes ALL content and returns `""`, the empty string is falsy and silently falls back to the original content. This is arguably safe behavior (you don't want to extract from empty text), but it masks the case where boilerplate removal was too aggressive. Consider `cleaned_content if cleaned_content is not None else content` if you want explicit control, or document the current behavior as intentional.

- [ ] **src/services/extraction/schema_orchestrator.py:175** — `populated_ratio` is unpacked but never used after confidence scaling was removed (commit `0c0c553`). Should use `is_empty, _ = self._is_empty_result(merged, group)` to signal intent. Ruff/linting will flag this.

## Minor

- [ ] **src/api/v1/dedup.py:66** — Exception detail is passed through to HTTP response: `detail=f"Boilerplate analysis failed: {e}"`. Could leak internal info (DB errors, file paths). Consider a generic message in the response with full details only in logs.

- [ ] **src/api/v1/dedup.py:23-37** — `analyze_boilerplate` POST endpoint takes all parameters as `Query()`. Non-idiomatic for POST — typically query params go on GET, request body on POST. Works fine but unusual for API consumers.

- [ ] **src/api/v1/dedup.py:90** — `get_boilerplate_stats` return type annotation is `dict` instead of a Pydantic response model. Won't affect runtime but OpenAPI docs will be untyped.

## Not Issues (False Alarms Investigated)

- **source.py:280 `stmt.excluded.metadata`** — This is CORRECT. `excluded` uses DB column names, and `meta_data` maps to DB column `"metadata"` via `mapped_column("metadata", ...)`. The code comment confirms this.

- **DomainDedupService.analyze_project() commits inside service** — This is intentional. `analyze_domain()` flushes per domain, `analyze_project()` commits once at the end. The API endpoint delegates transaction control to the service for this long-running operation. Consistent with the pattern used elsewhere for batch operations.

- **Migration quality** — Reviewed `20260226_add_domain_boilerplate.py`. Correct UUID types, proper FK with CASCADE, unique constraint, indexes, nullable cleaned_content on sources. Down migration is symmetric. No issues.
