# TODO: Smart Report Merging with LLM Synthesis

**Agent ID:** report-synthesis
**Branch:** feat/report-llm-synthesis
**Priority:** High

## Context

The report generation system currently uses rule-based aggregation that loses information:
- Boolean fields: `any()` (true if any source is true)
- Numeric fields: `max()` (just takes maximum)
- Text fields: longest value only OR semicolon-joined
- **No source attribution** - users can't see which page facts came from
- **LLMClient passed to ReportService but never used**

We need to use the LLM to intelligently synthesize facts from multiple pages while preserving source attribution.

## Objective

Implement LLM-based synthesis for report generation that intelligently merges extractions from multiple pages, preserves context, and includes source attribution.

---

## Tasks

### Task 1: Add Generic LLM Completion Method

**File:** `src/services/llm/client.py`

**Why:** The current `LLMClient` only has specialized methods (`extract_facts`, `extract_entities`). We need a generic completion method for synthesis prompts.

**Add this method to LLMClient:**

```python
async def complete(
    self,
    system_prompt: str,
    user_prompt: str,
    response_format: dict | None = None,
    temperature: float | None = None,
) -> dict:
    """Generic LLM completion for arbitrary prompts.

    Args:
        system_prompt: System message for the LLM.
        user_prompt: User message/query.
        response_format: Optional response format (e.g., {"type": "json_object"}).
        temperature: Optional temperature override.

    Returns:
        Parsed JSON response as dict.

    Raises:
        LLMExtractionError: If LLM call fails or returns invalid JSON.
    """
    if self.llm_queue is not None:
        return await self._complete_via_queue(
            system_prompt, user_prompt, response_format, temperature
        )
    return await self._complete_direct(
        system_prompt, user_prompt, response_format, temperature
    )

async def _complete_direct(
    self,
    system_prompt: str,
    user_prompt: str,
    response_format: dict | None = None,
    temperature: float | None = None,
) -> dict:
    """Direct LLM completion with retry logic."""
    import json

    max_retries = self.settings.llm_max_retries
    temp = temperature or self.settings.llm_base_temperature

    for attempt in range(1, max_retries + 1):
        try:
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temp,
                "max_tokens": self.settings.llm_max_tokens,
            }
            if response_format:
                kwargs["response_format"] = response_format

            response = await self.client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content

            # Parse as JSON if json_object format requested
            if response_format and response_format.get("type") == "json_object":
                return json.loads(content)
            return {"text": content}

        except Exception as e:
            if attempt == max_retries:
                raise LLMExtractionError(f"LLM completion failed: {e}") from e
            await asyncio.sleep(self.settings.llm_retry_backoff_min * (2 ** (attempt - 1)))

async def _complete_via_queue(
    self,
    system_prompt: str,
    user_prompt: str,
    response_format: dict | None = None,
    temperature: float | None = None,
) -> dict:
    """Queue-based LLM completion."""
    from services.llm.models import LLMRequest
    from services.llm.queue import QueueFullError, RequestTimeoutError

    request = LLMRequest(
        request_id=str(uuid4()),
        request_type="complete",
        payload={
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_format": response_format,
            "temperature": temperature,
            "model": self.model,
        },
        priority=5,
        created_at=datetime.now(UTC),
        timeout_at=datetime.now(UTC) + timedelta(seconds=300),
    )

    try:
        await self.llm_queue.submit(request)
        response = await self.llm_queue.wait_for_result(request.request_id, timeout=300)
    except (QueueFullError, RequestTimeoutError) as e:
        raise LLMExtractionError(f"LLM queue error: {e}") from e

    if response.status in ("error", "timeout"):
        raise LLMExtractionError(f"LLM completion failed: {response.error}")

    return response.result or {}
```

**Verification:**
- Write unit test `tests/test_llm_client.py::test_complete_returns_json`
- Test with mocked AsyncOpenAI client
- Test retry behavior on failure

---

### Task 2: Extend Data Gathering with Source Info

**Files:**
- `src/services/storage/repositories/extraction.py`
- `src/services/reports/service.py`

**Current code in repository `list()` method:**
```python
async def list(self, filters: ExtractionFilters, limit: int | None = None, offset: int = 0) -> list[Extraction]:
```

**Required changes:**

1. **Update ExtractionRepository.list()** to support eager-loading:

```python
async def list(
    self,
    filters: ExtractionFilters,
    limit: int | None = None,
    offset: int = 0,
    include_source: bool = False,  # NEW
) -> list[Extraction]:
    """List extractions with optional filtering.

    Args:
        filters: ExtractionFilters instance
        limit: Maximum results
        offset: Results to skip
        include_source: If True, eager-load source relationship

    Returns:
        List of Extraction instances
    """
    from sqlalchemy.orm import joinedload

    query = select(Extraction)

    # Eager-load source if requested
    if include_source:
        query = query.options(joinedload(Extraction.source))

    # ... rest of existing filter logic
```

2. **Update `_gather_data()` in ReportService** to include source info:

```python
# In _gather_data(), update the repository call:
extractions = await self._extraction_repo.list(
    filters=filters,
    limit=max_extractions,
    offset=0,
    include_source=True,  # NEW
)

# Update the extraction dict to include source info:
extractions_by_group[source_group] = [
    {
        "id": str(ext.id),
        "data": ext.data,
        "confidence": ext.confidence,
        "extraction_type": ext.extraction_type,
        "source_id": str(ext.source_id),
        "source_uri": ext.source.uri if ext.source else None,      # NEW
        "source_title": ext.source.title if ext.source else None,  # NEW
        "chunk_index": ext.chunk_index,
    }
    for ext in extractions
]
```

**Verification:**
- Write test that verifies extractions include `source_uri` and `source_title`
- Existing tests should still pass

---

### Task 3: Create LLM Synthesis Service

**New File:** `src/services/reports/synthesis.py`

Create a new service that uses LLMClient for intelligent fact synthesis.

```python
"""LLM-based synthesis for report generation."""

import structlog
from dataclasses import dataclass

from services.llm.client import LLMClient, LLMExtractionError

logger = structlog.get_logger(__name__)


@dataclass
class SynthesisResult:
    """Result from LLM synthesis."""

    synthesized_text: str
    sources_used: list[str]  # URIs
    confidence: float
    conflicts_noted: list[str]


@dataclass
class MergeResult:
    """Result from field value merging."""

    value: str | int | float | bool | list
    sources: list[str]  # URIs
    confidence: float


class ReportSynthesizer:
    """LLM-based synthesis for report generation."""

    # Max extractions per synthesis call to avoid token limits
    MAX_FACTS_PER_SYNTHESIS = 15

    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    async def synthesize_facts(
        self,
        facts: list[dict],  # Each has: data, confidence, source_uri, source_title
        synthesis_type: str = "summarize",  # "summarize", "compare", "aggregate"
    ) -> SynthesisResult:
        """
        Synthesize multiple facts into coherent output with attribution.

        Args:
            facts: List of extraction dicts with source info
            synthesis_type: How to combine - summarize, compare, or aggregate

        Returns:
            SynthesisResult with combined text and source attribution
        """
        if not facts:
            return SynthesisResult(
                synthesized_text="No facts available.",
                sources_used=[],
                confidence=0.0,
                conflicts_noted=[],
            )

        # Chunk if too many facts
        if len(facts) > self.MAX_FACTS_PER_SYNTHESIS:
            return await self._synthesize_chunked(facts, synthesis_type)

        # Build facts text for prompt
        facts_text = self._format_facts_for_prompt(facts)

        system_prompt = f"""You are synthesizing extracted facts from multiple source documents.

Facts to synthesize:
{facts_text}

Instructions:
1. Combine related facts into coherent statements
2. When facts conflict, note the discrepancy and prefer higher confidence sources
3. Preserve key details and specifics from each source
4. Include source attribution in brackets [Source: page_title]

Output as JSON:
{{
  "synthesized_text": "Combined fact with [Source: title] attribution...",
  "sources_used": ["uri1", "uri2"],
  "confidence": 0.85,
  "conflicts_noted": ["description of any conflicts found"]
}}"""

        user_prompt = f"Synthesize these facts using '{synthesis_type}' approach."

        try:
            result = await self._llm.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
            )
            return SynthesisResult(
                synthesized_text=result.get("synthesized_text", ""),
                sources_used=result.get("sources_used", []),
                confidence=result.get("confidence", 0.8),
                conflicts_noted=result.get("conflicts_noted", []),
            )
        except LLMExtractionError as e:
            logger.warning("llm_synthesis_failed", error=str(e))
            return self._fallback_synthesis(facts)

    async def _synthesize_chunked(
        self,
        facts: list[dict],
        synthesis_type: str,
    ) -> SynthesisResult:
        """Synthesize large fact sets by chunking."""
        chunks = [
            facts[i : i + self.MAX_FACTS_PER_SYNTHESIS]
            for i in range(0, len(facts), self.MAX_FACTS_PER_SYNTHESIS)
        ]

        # Synthesize each chunk
        chunk_results = []
        for chunk in chunks:
            result = await self.synthesize_facts(chunk, synthesis_type)
            chunk_results.append(result)

        # Combine chunk results
        all_text = "\n\n".join(r.synthesized_text for r in chunk_results)
        all_sources = list(set(s for r in chunk_results for s in r.sources_used))
        all_conflicts = [c for r in chunk_results for c in r.conflicts_noted]
        avg_confidence = sum(r.confidence for r in chunk_results) / len(chunk_results)

        return SynthesisResult(
            synthesized_text=all_text,
            sources_used=all_sources,
            confidence=avg_confidence,
            conflicts_noted=all_conflicts,
        )

    def _fallback_synthesis(self, facts: list[dict]) -> SynthesisResult:
        """Rule-based fallback when LLM fails."""
        # Join facts with bullet points
        lines = []
        sources = set()
        for fact in facts:
            data = fact.get("data", {})
            text = data.get("fact", str(data))
            source_title = fact.get("source_title", "Unknown")
            lines.append(f"- {text} [Source: {source_title}]")
            if fact.get("source_uri"):
                sources.add(fact["source_uri"])

        return SynthesisResult(
            synthesized_text="\n".join(lines),
            sources_used=list(sources),
            confidence=0.7,
            conflicts_noted=["Fallback: LLM synthesis unavailable"],
        )

    def _format_facts_for_prompt(self, facts: list[dict]) -> str:
        """Format facts for LLM prompt."""
        lines = []
        for i, fact in enumerate(facts, 1):
            data = fact.get("data", {})
            text = data.get("fact", str(data))
            confidence = fact.get("confidence", 0.8)
            source = fact.get("source_title", "Unknown")
            uri = fact.get("source_uri", "")
            lines.append(
                f"{i}. \"{text}\" (confidence: {confidence:.2f}, source: {source}, uri: {uri})"
            )
        return "\n".join(lines)

    async def merge_field_values(
        self,
        field_name: str,
        values: list[dict],  # Each has: value, source_uri, confidence
        field_type: str,  # "boolean", "text", "number", "list"
    ) -> MergeResult:
        """
        Merge values for a single field with LLM intelligence.

        For simple types (boolean, number), use rule-based logic.
        For text fields, use LLM to synthesize intelligently.
        For lists, deduplicate then optionally summarize if too long.
        """
        if not values:
            return MergeResult(value=None, sources=[], confidence=0.0)

        sources = [v.get("source_uri") for v in values if v.get("source_uri")]

        if field_type == "boolean":
            # Use any() - True if ANY source says True
            merged_val = any(v.get("value") for v in values)
            return MergeResult(
                value=merged_val,
                sources=sources,
                confidence=max(v.get("confidence", 0.8) for v in values),
            )

        elif field_type in ("number", "integer", "float"):
            # Use max
            numeric_vals = [v.get("value") for v in values if v.get("value") is not None]
            merged_val = max(numeric_vals) if numeric_vals else None
            return MergeResult(
                value=merged_val,
                sources=sources,
                confidence=max(v.get("confidence", 0.8) for v in values),
            )

        elif field_type == "list":
            # Deduplicate
            flat = []
            for v in values:
                val = v.get("value", [])
                if isinstance(val, list):
                    flat.extend(val)
                else:
                    flat.append(val)
            unique = list(dict.fromkeys(str(x) for x in flat))
            return MergeResult(
                value=unique,
                sources=sources,
                confidence=0.9,
            )

        else:
            # Text - use LLM synthesis
            return await self._synthesize_text(field_name, values)

    async def _synthesize_text(
        self,
        field_name: str,
        texts: list[dict],
    ) -> MergeResult:
        """Use LLM to intelligently combine text values."""
        unique_texts = list(dict.fromkeys(
            str(t.get("value", "")) for t in texts if t.get("value")
        ))

        # If only one unique value, just return it
        if len(unique_texts) <= 1:
            return MergeResult(
                value=unique_texts[0] if unique_texts else None,
                sources=[t.get("source_uri") for t in texts if t.get("source_uri")],
                confidence=0.95,
            )

        # Multiple unique values - use LLM to merge
        texts_formatted = "\n".join(
            f'- "{t.get("value")}" (confidence: {t.get("confidence", 0.8):.2f}, source: {t.get("source_uri", "unknown")})'
            for t in texts
        )

        system_prompt = f"""Merge these text values for the field "{field_name}":

{texts_formatted}

Instructions:
- Combine into a single coherent text
- Preserve important details from each source
- If values conflict, prefer higher confidence sources
- Keep the result concise

Output as JSON:
{{
  "merged_text": "Combined text here",
  "sources_used": ["uri1", "uri2"],
  "confidence": 0.9
}}"""

        try:
            result = await self._llm.complete(
                system_prompt=system_prompt,
                user_prompt="Merge these text values.",
                response_format={"type": "json_object"},
            )
            return MergeResult(
                value=result.get("merged_text", unique_texts[0]),
                sources=result.get("sources_used", []),
                confidence=result.get("confidence", 0.85),
            )
        except LLMExtractionError as e:
            logger.warning("llm_text_merge_failed", field=field_name, error=str(e))
            # Fallback: take longest
            longest = max(unique_texts, key=len) if unique_texts else None
            return MergeResult(
                value=longest,
                sources=[t.get("source_uri") for t in texts if t.get("source_uri")],
                confidence=0.7,
            )
```

**Verification:**
- Write unit tests in `tests/test_report_synthesis.py`
- Test each method with mocked LLMClient
- Test fallback behavior when LLM fails
- Test chunking for large fact sets

---

### Task 4: Update ReportService to Use Synthesizer

**File:** `src/services/reports/service.py`

#### 4a. Update constructor to accept synthesizer

```python
from services.reports.synthesis import ReportSynthesizer

class ReportService:
    def __init__(
        self,
        extraction_repo: ExtractionRepository,
        entity_repo: EntityRepository,
        llm_client: LLMClient,
        db_session,
        synthesizer: ReportSynthesizer | None = None,  # NEW - injectable
    ):
        self._extraction_repo = extraction_repo
        self._entity_repo = entity_repo
        self._llm_client = llm_client
        self._db = db_session
        # Create synthesizer if not provided (for backward compatibility)
        self._synthesizer = synthesizer or ReportSynthesizer(llm_client)
```

#### 4b. Update `_generate_single_report()`

Replace the current simple grouping with LLM synthesis:

```python
async def _generate_single_report(
    self,
    data: ReportData,
    title: str | None,
) -> str:
    """Generate markdown for single source_group report with LLM synthesis."""
    source_group = data.source_groups[0]
    extractions = data.extractions_by_group.get(source_group, [])

    if not title:
        title = f"{source_group} - Extraction Report"

    lines = [
        f"# {title}",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Extractions: {len(extractions)}",
        "",
    ]

    # Group by extraction_type
    by_category: dict[str, list[dict]] = {}
    for ext in extractions:
        category = ext.get("extraction_type", "General")
        by_category.setdefault(category, []).append(ext)

    # Synthesize each category
    sources_referenced: set[str] = set()
    for category, items in sorted(by_category.items()):
        result = await self._synthesizer.synthesize_facts(items, synthesis_type="summarize")
        lines.append(f"## {category}")
        lines.append("")
        lines.append(result.synthesized_text)
        lines.append("")
        sources_referenced.update(result.sources_used)

        # Note conflicts if any
        if result.conflicts_noted:
            lines.append("*Note: " + "; ".join(result.conflicts_noted) + "*")
            lines.append("")

    # Add sources section
    lines.append("## Sources Referenced")
    lines.append("")
    for ext in extractions:
        uri = ext.get("source_uri")
        title_text = ext.get("source_title", uri)
        if uri and uri in sources_referenced:
            lines.append(f"- [{title_text}]({uri})")

    return "\n".join(lines)
```

#### 4c. Add helper for sources section

```python
def _build_sources_section(
    self,
    uris: set[str],
    extractions: list[dict],
) -> str:
    """Build markdown sources section."""
    uri_to_title = {}
    for ext in extractions:
        if ext.get("source_uri") and ext.get("source_title"):
            uri_to_title[ext["source_uri"]] = ext["source_title"]

    lines = ["### Sources Referenced", ""]
    for uri in sorted(uris):
        title = uri_to_title.get(uri, uri)
        lines.append(f"- [{title}]({uri})")

    return "\n".join(lines)
```

#### 4d. Update API endpoint to pass synthesizer (optional)

**File:** `src/api/v1/reports.py`

```python
# In create_report endpoint, synthesizer is auto-created by ReportService
# No changes needed for basic functionality

# For testing, you can inject a mock:
# synthesizer = MockReportSynthesizer()
# report_service = ReportService(..., synthesizer=synthesizer)
```

**Verification:**
- Update existing tests in `tests/test_report_service.py`
- Mock the synthesizer in tests using the injectable parameter
- Verify source citations appear in output

---

### Task 5: Update SchemaTableReport (Optional Enhancement)

**File:** `src/services/reports/schema_table.py`

**Note:** SchemaTableReport currently uses deprecated `FIELD_GROUPS_BY_NAME`. This task adds basic synthesis support but does NOT fix the underlying deprecation issue.

#### 5a. Add optional synthesizer support

```python
from services.reports.synthesis import ReportSynthesizer

class SchemaTableReport:
    def __init__(
        self,
        db_session: Session,
        synthesizer: ReportSynthesizer | None = None,  # Optional
    ):
        self._db = db_session
        self._synthesizer = synthesizer
```

#### 5b. Update text field merging (synchronous fallback)

Since SchemaTableReport uses sync DB queries, we keep the current behavior but add async synthesis support when a synthesizer is provided:

```python
def _merge_field_group_data(self, data_list: list[dict], group) -> dict[str, Any]:
    """Merge multiple extraction data dicts for a field group."""
    merged = {}

    for field in group.fields:
        values = [
            d.get(field.name) for d in data_list if d.get(field.name) is not None
        ]

        if not values:
            merged[field.name] = None
            continue

        if field.field_type == "boolean":
            merged[field.name] = any(values)
        elif field.field_type in ("integer", "float"):
            merged[field.name] = max(values)
        elif field.field_type == "list":
            # ... existing dedup logic
            pass
        else:
            # Text - use existing semicolon join (sync path)
            # LLM synthesis would require async refactor
            unique = list(dict.fromkeys([str(v) for v in values if v]))
            merged[field.name] = (
                "; ".join(unique) if len(unique) > 1 else (unique[0] if unique else None)
            )

    return merged
```

**Note:** Full async synthesis in SchemaTableReport would require converting the entire class to async, including the SQLAlchemy queries. This is a larger refactor deferred to a future task.

**Verification:**
- Existing tests should still pass
- Add test for synthesizer parameter (even if unused in sync path)

---

### Task 6: Add Provenance to Report Output

**File:** `src/services/reports/service.py`

Ensure all report types include source attribution in markdown output.

**Example output format:**
```markdown
## Technical Facts

- REMPCO lead screws require minimal maintenance [Source: Lead Screws Page]
- Two-stage coupling process for extra long screws [Source: Manufacturing Page]

### Sources Referenced
- [Lead Screws Page](https://rempco.com/lead-screws)
- [Manufacturing Page](https://rempco.com/manufacturing)
```

**Verification checklist:**
1. Single reports have sources section
2. Comparison reports have sources section per group
3. Source URIs are correctly linked

---

### Task 7: Update API Response Model (Optional)

**File:** `src/models.py`

Extend ReportResponse to include provenance information:

```python
class ReportResponse(BaseModel):
    """Response model for report."""

    id: str
    type: str
    title: str
    content: str
    source_groups: list[str]
    extraction_count: int
    entity_count: int
    sources_referenced: list[str] | None = None  # NEW - list of source URIs
    generated_at: str

    class Config:
        from_attributes = True
```

**Update `src/api/v1/reports.py`:**

```python
# When building ReportResponse, extract source URIs from content
# (They're embedded in markdown, so this is optional enhancement)
```

**Verification:**
- Update API tests to check new field
- Verify backward compatibility (field is optional)

---

## Tests to Write

| Test File | Tests |
|-----------|-------|
| `tests/test_llm_client.py` | Test `complete()` method with mock |
| `tests/test_report_synthesis.py` | **NEW** - Test ReportSynthesizer class |
| `tests/test_report_service.py` | Update for synthesizer injection |
| `tests/test_report_endpoint.py` | Verify source attribution in output |

### Test Cases for `test_report_synthesis.py`:

```python
class TestReportSynthesizer:
    async def test_synthesize_facts_combines_with_attribution(self):
        """Verify facts are combined with [Source: X] citations."""

    async def test_synthesize_facts_notes_conflicts(self):
        """Verify conflicting facts are noted."""

    async def test_synthesize_facts_chunks_large_inputs(self):
        """Verify large fact sets are chunked to avoid token limits."""

    async def test_merge_field_values_boolean(self):
        """Verify boolean uses any()."""

    async def test_merge_field_values_number(self):
        """Verify number uses max."""

    async def test_merge_field_values_text_uses_llm(self):
        """Verify text fields call LLM for synthesis."""

    async def test_merge_field_values_list_deduplicates(self):
        """Verify list values are deduplicated."""

    async def test_fallback_on_llm_failure(self):
        """Verify graceful fallback when LLM fails."""

    async def test_empty_facts_returns_empty_result(self):
        """Verify empty input is handled."""
```

---

## Constraints

1. **DO NOT** break existing report generation - all current functionality must work
2. **DO NOT** make LLM calls mandatory - fall back gracefully if LLM fails
3. **DO NOT** change the Report ORM schema (extraction_ids field already exists)
4. **DO NOT** modify field_groups.py (it's deprecated)
5. **DO NOT** convert SchemaTableReport to fully async (larger refactor)
6. **DO** preserve backward compatibility in API responses
7. **DO** keep existing aggregation logic as fallback
8. **DO** add comprehensive error handling
9. **DO** chunk large fact sets to avoid token limits (max 15 per synthesis)
10. **DO** make synthesizer injectable for testability

---

## Verification Checklist

Before creating PR, verify:

- [ ] All existing tests pass: `pytest tests/ -v`
- [ ] New tests pass: `pytest tests/test_report_synthesis.py tests/test_llm_client.py -v`
- [ ] Linting passes: `ruff check . && ruff format --check .`
- [ ] Type checking passes: `mypy src/`
- [ ] Single report includes source citations
- [ ] Comparison report includes source citations
- [ ] LLM failures fall back gracefully (test by mocking failure)
- [ ] Large fact sets are chunked (test with 20+ facts)
- [ ] `LLMClient.complete()` works in both direct and queue modes

---

## Files Modified Summary

| File | Type | Changes |
|------|------|---------|
| `src/services/llm/client.py` | MODIFY | Add `complete()` method |
| `src/services/reports/synthesis.py` | NEW | LLM synthesis service |
| `src/services/reports/service.py` | MODIFY | Inject synthesizer, add source info |
| `src/services/reports/schema_table.py` | MODIFY | Optional synthesizer param |
| `src/services/storage/repositories/extraction.py` | MODIFY | `include_source` option |
| `src/models.py` | MODIFY | Add `sources_referenced` field |
| `src/api/v1/reports.py` | MODIFY | Minor updates for new fields |
| `tests/test_llm_client.py` | MODIFY | Test `complete()` |
| `tests/test_report_synthesis.py` | NEW | Synthesizer unit tests |
| `tests/test_report_service.py` | MODIFY | Mock synthesizer |

---

## Implementation Order

1. **Task 1** - Add `LLMClient.complete()` (required foundation)
2. **Task 2** - Source attribution in data gathering
3. **Task 3** - Create synthesis service
4. **Task 4** - Integrate synthesizer into ReportService
5. **Task 6** - Verify provenance in output
6. **Task 5** - SchemaTableReport (optional, sync-only)
7. **Task 7** - API response extension (optional polish)
8. Write tests throughout each task

---

## Git Workflow

```bash
# Start
git checkout main
git pull origin main
git checkout -b feat/report-llm-synthesis

# After each task, commit:
git add -A
git commit -m "feat(reports): <description of task>"

# When complete:
git push origin feat/report-llm-synthesis
gh pr create --title "feat: Smart report merging with LLM synthesis" --body "..."
```

---

## Known Limitations

1. **SchemaTableReport remains sync** - Full async would require larger refactor
2. **FIELD_GROUPS_BY_NAME still deprecated** - Not addressed in this PR
3. **No caching** - Each report regenerates synthesis (future: cache by extraction IDs)
4. **No cost tracking** - LLM costs not monitored (future: add metrics)
