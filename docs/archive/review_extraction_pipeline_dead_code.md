# Pipeline Review: Extraction Pipeline — Dead & Obsolete Code

**Scope:** `src/services/extraction/` (18 files) + callers in `scheduler.py`, `worker.py`, `service_container.py`
**Date:** 2026-03-04
**Method:** Full read of all source files, grep for all cross-references, call graph tracing

## Architecture Summary

Two parallel extraction paths exist:

```
ExtractionWorker.process_job()
├── has_schema=True → SchemaExtractionPipeline → SchemaExtractionOrchestrator → SchemaExtractor
└── has_schema=False → ExtractionPipelineService → ExtractionOrchestrator → LLMClient.extract_facts()
```

The schema path is used by ALL template-created projects. The generic path is a fallback for projects without `extraction_schema.field_groups`.

---

## Critical (must fix)

*(none found)*

## Important (should fix)

### 1. `EXTRACTION_CONTENT_LIMIT` — deprecated module-level constant
- **File:** `src/services/extraction/schema_extractor.py:27`
- **Status:** Explicitly marked deprecated in comment
- **Problem:** Frozen at import time from `settings.extraction_content_limit`. If config changes at runtime, this stale value persists.
- **Used by:** `src/services/llm/worker.py:97` (as fallback default)
- **Fix:** Replace `LLMWorker` fallback with `settings.extraction.content_limit` or accept it as required constructor param. Then delete the constant and the `_settings_singleton` import (line 12).

### 2. `get_extraction_content()` hardcoded `domain_dedup_enabled=True` in generic pipeline
- **File:** `src/services/extraction/pipeline.py:147`
- **Code:** `content = get_extraction_content(source)` — no explicit param
- **Problem:** Generic pipeline ignores config's `domain_dedup_enabled` setting. The schema pipeline correctly passes it (line 478). This is a bug — if domain dedup is disabled in config, the generic pipeline still uses cleaned_content.
- **Fix:** Pass `domain_dedup_enabled=self._extraction_config.domain_dedup_enabled` (requires plumbing config into `ExtractionPipelineService`), or leave as-is since generic pipeline is rarely used.

### 3. Redundant else branch in `PageClassifier.classify()`
- **File:** `src/services/extraction/page_classifier.py:100-102`
- **Code:**
  ```python
  if self._method == ClassificationMethod.RULE_BASED:
      result = self._classify_rule_based(url, title)
  else:
      # Future: LLM-assisted classification
      result = self._classify_rule_based(url, title)  # ← identical call
  ```
- **Problem:** Both branches do the same thing. The else branch is unreachable in practice since all callers use the default `RULE_BASED` method. `SmartClassifier` wraps its own `PageClassifier(method=RULE_BASED)`.
- **Fix:** Remove the else branch, just call `_classify_rule_based()` unconditionally.

### 4. `ClassificationMethod.LLM_ASSISTED` — unused enum value
- **File:** `src/services/extraction/page_classifier.py:16`
- **Code:** `LLM_ASSISTED = "llm"  # Future`
- **Used by:** Only `tests/test_page_classifier.py:383` (asserts enum value exists)
- **Fix:** Remove enum value and the test assertion. `HYBRID` is actively used by `SmartClassifier`.

## Minor

### 5. `TemplateCrawlConfig` / `TemplateClassificationConfig` backward-compat aliases
- **File:** `src/services/extraction/schema_adapter.py:70,181`
- **Code:**
  ```python
  ClassificationConfig = TemplateClassificationConfig  # line 70
  CrawlConfig = TemplateCrawlConfig  # line 181
  ```
- **Status:** Actively used via the aliases (`worker.py:189`, `smart_classifier.py:25`). These are needed for now but should be cleaned up in a future pass — callers should import the `Template*` names directly.

### 6. Comment references to removed code
- **File:** `src/services/extraction/schema_extractor.py:25-26`
- **Code:** `# Deprecated: frozen at import time. Use config.settings.extraction_content_limit instead.`
- **Status:** The comment is accurate but the constant should just be deleted (see #1).

### 7. `schema_orchestrator.py:161` — stale comment reference
- **Code:** `# within EXTRACTION_CONTENT_LIMIT (both are ~4 chars/token aligned)`
- **Problem:** References the deprecated constant name. Should reference `extraction.content_limit` or `extraction.chunk_max_tokens`.

---

## NOT Dead (confirmed active, do not remove)

These were investigated but confirmed to be actively used:

| Component | Status | Rationale |
|-----------|--------|-----------|
| `ExtractionPipelineService` | Active fallback | Used by `ExtractionWorker` for projects without schemas |
| `ExtractionOrchestrator` (extractor.py) | Active fallback | Used by generic pipeline via `scheduler.py:373` |
| `ProfileRepository` | Active fallback | Used by generic pipeline via `scheduler.py:386` |
| `DEFAULT_PROFILE` (pipeline.py:27) | Active fallback | Fallback when profile not found in DB |
| `BackpressureManager` | Active | Used by generic pipeline AND available for schema pipeline |
| `embed_facts()` | Active | Used by generic pipeline (pipeline.py:194) |
| `embed_and_upsert()` | Active | Used by schema pipeline (pipeline.py:744) |
| `ExtractionDeduplicator` | Active | In ServiceContainer, used by generic pipeline |
| `EntityExtractor` | Active | Created in scheduler.py, used by generic pipeline |
| `ExtractedFact`/`ExtractionResult`/`ExtractionProfile` models | Active | Used by generic pipeline and LLMClient |
| `content_cleaner.py` | Active | `strip_structural_junk` used by SchemaExtractor, `clean_markdown_for_embedding` by SmartClassifier |
| `ClassificationMethod.HYBRID` | Active | Used extensively by SmartClassifier |
| `SmartClassificationResult` | Active | Returned by SmartClassifier methods |

## Future Consideration: Generic Pipeline Deprecation

The entire generic pipeline subsystem is a parallel extraction system:
- `ExtractionPipelineService` + `ExtractionOrchestrator` + `LLMClient.extract_facts()`
- `ProfileRepository` + `ExtractionProfile` + `ExtractionDeduplicator` + `EntityExtractor`
- `embed_facts()` on `ExtractionEmbeddingService`

All projects created via templates (or MCP) get a schema. The generic path only fires for schema-less projects. A future iteration could:
1. Make `DEFAULT_EXTRACTION_TEMPLATE` the universal fallback in `SchemaExtractionPipeline`
2. Remove the generic pipeline entirely
3. Simplify `ExtractionWorker` to always use schema path

This is a **major refactor** (touches scheduler, worker, pipeline, models, 10+ test files) and should NOT be done in this cleanup pass. Flagged for future planning.
