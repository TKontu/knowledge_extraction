# Extraction Architecture v2 — Implementation Plan

**2026-03-07. Per-item extraction with complete grounding chain.**

## Design Principles

1. Every extracted data item is independent with own confidence, quote, source location, grounding score
2. Grounding is confirmed for every item inline (not deferred to backfill)
3. Multi-answer fields are extracted iteratively — pagination is unbounded (driven by content, not hardcoded caps)
4. Compound types (entity + attributes) are extracted and grounded as a unit
5. All metadata flows cleanly to consolidation
6. **Chunking is a processing detail, not a storage structure** — storage is per-source, chunking parameters are freely adjustable

---

## Three Answer Cardinalities

Every field in the schema falls into one of three cardinalities. The extraction, merge, and storage strategy differs for each:

### 1. Single-Answer Fields

Fields where one source has one correct value.

**Examples**: `company_name` (text), `employee_count` (integer), `headquarters_location` (text), `employee_count_range` (enum), `manufactures_gearboxes` (boolean)

**Extraction**: LLM produces one `{value, confidence, quote}` per chunk. Multiple chunks may produce different values.

**Merge**: Keep the best item (highest grounding * confidence). Store losing candidates as `alternatives` for consolidation visibility.

**Storage**: Single `FieldItem` with optional `alternatives` list.

### 2. Multi-Value Fields

Fields that are lists of independent values. Each list item is a simple value (string, number) with its own provenance.

**Examples**: `certifications` (list: "ISO 9001", "ISO 14001", "ATEX"), `service_types` (list: "repair", "maintenance", "field service"), `locations` (list of dicts)

**Extraction**: LLM produces a list of items per chunk. Different chunks contribute different items.

**Merge**: Union all items from all chunks. Dedup. Each individual item carries its own quote and grounding.

**Storage**: List of `ListValueItem`, each with `{value, confidence, quote, grounding, location}`.

### 3. Entity Lists

Compound objects where the entity is the unit — entity + attributes extracted together, grounded as "does this entity exist?"

**Examples**: `products_gearbox` (entity list: product_name + series + power + torque + ratio), `products_motor`, `products_accessory`

**Extraction**: LLM produces entities per chunk. A single chunk may contain many entities (product catalog page with 50+ products). Pagination via `has_more` signal — **no hardcoded iteration cap**. Extraction continues until:
- LLM returns `has_more: false`
- No new (non-duplicate) entities found in last iteration
- Stall detection: LLM returned only duplicates N times consecutively
- Schema-defined `max_items` reached

**Merge**: Union all entities from all chunks and all iterations. Dedup by ID fields, keeping better-grounded version.

**Storage**: List of `EntityItem`, each with `{fields, confidence, quote, grounding, location}`.

### 4. Summary Fields

Synthesized text that describes or summarizes knowledge from the source. Not extracted verbatim — the LLM generates a coherent description based on evidence found across the content.

**Examples**: `company_description` (summary of what the company does), `manufacturing_details` (summary of manufacturing capabilities), `service_overview`

**Why this is distinct from text/single-answer**:
- **Not verbatim**: A quote doesn't "prove" a summary — the summary is a synthesis, not an extraction
- **Cross-chunk**: A good summary should reflect knowledge from the entire source, not just one chunk
- **Grounding mode**: Always "none" — string-match grounding is meaningless for synthesized text
- **Quality signal**: Confidence reflects how much relevant material was available, not whether a specific string was found

**Extraction**: Each chunk produces a partial summary (what this chunk contributes to the overall picture). The LLM returns `{value, confidence}` — no quote required.

**Merge**: Dedicated summarization merge — not "pick best" (loses information) and not "concat" (produces incoherent text). Two options:

1. **Longest-with-high-confidence** (no extra LLM call): Take the chunk summary with the highest `confidence * length_score`. This works because the chunk with the most relevant content will produce the most comprehensive partial summary. Simple, fast, no extra cost.

2. **LLM synthesis** (extra LLM call, higher quality): After extracting all chunks, make one additional LLM call with all partial summaries concatenated, asking it to synthesize a coherent combined summary. This is the only merge strategy that uses an LLM call — all other merges are pure data operations.

Default: option 1 (longest-with-high-confidence). Option 2 available as `merge_strategy: "llm_synthesize"` on the field definition for schemas where summary quality is critical.

**Storage**: Single `FieldItem` with `grounding: null` (not applicable). The `quote` field stores a brief "evidence fingerprint" — not a verbatim excerpt, but a short note about what source sections contributed (e.g., "Based on: About Us, Products, Manufacturing" derived from heading paths of contributing chunks).

**Schema definition**:
```yaml
- name: company_description
  field_type: summary
  description: "Brief description of the company's core business, products, and market position"
  merge_strategy: longest_confident   # or: llm_synthesize
```

**What this means for the grounding pipeline**: Summary fields are skipped entirely (`grounding_mode = "none"`). They have no quote to verify. Their quality comes from the richness of the source content (reflected in confidence) and, optionally, from the LLM synthesis step.

**What this means for consolidation**: Summary fields from multiple sources (10-26 extractions per entity) use `longest_top_k` strategy (already exists) — select the longest, most detailed summaries weighted by confidence. Since there's no grounding score, weight = confidence only.

---

## Architecture Overview

### Current Flow
```
Source -> chunk_document() -> [chunks]
  Per chunk:
    LLM(all_fields) -> {field1: v, field2: v, confidence: 0.8, _quotes: {...}}
    compute_chunk_grounding(result, chunk)     # quote-in-source only
  Merge chunks:
    per-field strategy (highest_confidence / any_true / merge_dedupe)
    average confidence across chunks
    quote from highest-confidence chunk (may differ from value's chunk)
  Store:
    1 Extraction per (source, field_group) with merged JSON blob
```

### Target Flow
```
Source -> chunk_document() -> [chunks]   (chunking = processing only, not stored)
  Per chunk:
    LLM(field_group) -> per-field {value, confidence, quote}
    Per field item:
      locate quote in full source content      # compute source_location
      verify quote-in-source(quote, source)    # Layer A (against FULL source)
      verify value-in-quote(value, quote)      # Layer B
      -> item.grounding = min(A, B)
    For entity lists with has_more=true:
      Re-extract with exclusion list (until exhausted)
  Merge:
    Single-answer fields: best item wins, alternatives preserved
    Multi-value fields: union all items, per-item provenance
    Entity lists: union all entities, per-entity provenance
  Store:
    1 Extraction per (source, field_group) with per-item structured data
    Every item carries {value, confidence, quote, grounding, source_location}
```

### Key Insight: Chunking as Processing Layer

Storage is 1 Extraction per (source, field_group) — same cardinality as today. Chunks are ephemeral windows for LLM context limits. Each field item records WHERE in the source its knowledge lives (heading path + character offset), computed against the full source content. Chunk parameters (size, overlap, strategy) can be tuned freely without affecting stored data or requiring re-migration.

---

## Source Location Model

Each extracted item points back to where in the source it came from. The location must be:
- **Chunk-independent**: survives re-chunking with different parameters
- **Content-stable**: survives minor content cleaning changes
- **Human-readable**: useful in UI/reports for provenance display
- **Machine-resolvable**: can highlight the relevant section programmatically

### SourceLocation

```python
@dataclass(frozen=True)
class SourceLocation:
    """Where in the source content a piece of knowledge was found."""

    # Semantic location — human-readable, chunk-independent
    heading_path: list[str] | None    # e.g., ["Products", "Motor Series X"]

    # Precise location — machine-resolvable, chunk-independent
    char_offset: int | None           # start position of quote in source content
    char_end: int | None              # end position of quote in source content

    # Processing metadata — informational only, NOT structural
    chunk_index: int | None = None    # which chunk produced this (for debugging/analytics)
```

**heading_path** comes from finding the quote in the full source and walking backward to the nearest heading hierarchy. Falls back to the chunk's `header_path` (already computed by `chunk_document()` via `extract_header_path()`). Survives re-chunking because headings are structural features of the source.

**char_offset / char_end** computed by finding the quote string in the full source content. Absolute position, independent of chunking.

**chunk_index** is analytics metadata only — never used for logic.

### Computing Source Location

```python
def locate_in_source(
    quote: str,
    source_content: str,
    chunk_header_path: list[str] | None,
    chunk_index: int,
) -> SourceLocation:
    """Find where a quote appears in the full source content."""
    char_offset, char_end = _find_quote_position(quote, source_content)

    if char_offset is not None:
        heading_path = _heading_at_position(source_content, char_offset)
    else:
        heading_path = chunk_header_path

    return SourceLocation(
        heading_path=heading_path,
        char_offset=char_offset,
        char_end=char_end,
        chunk_index=chunk_index,
    )
```

**_find_quote_position**: Reuses normalized matching from `verify_quote_in_source()` but returns position. Tries exact → punctuation-stripped → sliding window.

**_heading_at_position**: Scans backward from `char_offset` in source markdown for nearest heading lines. Returns breadcrumb path from the source itself (not from chunk, which may merge sections).

---

## Data Model

### Item Types

```python
@dataclass(frozen=True)
class FieldItem:
    """Single extracted value with full provenance. Used for single-answer fields."""
    value: Any
    confidence: float
    quote: str | None = None
    grounding: float | None = None
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class ListValueItem:
    """One item from a multi-value list field, with its own provenance."""
    value: Any
    confidence: float
    quote: str | None = None
    grounding: float | None = None
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class EntityItem:
    """Single extracted entity (compound type) with full provenance.
    Grounding is at entity level — 'does this entity exist in the source?'
    Individual attributes are NOT grounded separately."""
    fields: dict[str, Any]
    confidence: float
    quote: str | None = None
    grounding: float | None = None
    source_location: SourceLocation | None = None
```

`FieldItem` and `ListValueItem` are structurally identical but semantically distinct — they represent different cardinalities and have different merge behavior. Could be the same class with a type tag, but separate classes make the merge dispatch explicit.

### ChunkExtractionResult

```python
@dataclass
class ChunkExtractionResult:
    """Result from extracting one field group from one chunk."""
    extraction_type: str
    chunk_index: int

    # Single-answer fields: field_name -> FieldItem
    items: dict[str, FieldItem]

    # Multi-value fields: field_name -> list of ListValueItem
    list_items: dict[str, list[ListValueItem]]

    # Entity lists: list of EntityItem (only for is_entity_list groups)
    entities: list[EntityItem] | None = None
    has_more: bool = False    # entity pagination signal
```

### Extraction.data Format v2

**Single-answer fields** — one item, optional alternatives:
```json
{
  "company_name": {
    "value": "Acme Corporation AG",
    "confidence": 0.9,
    "quote": "Acme Corporation AG is a leading...",
    "grounding": 1.0,
    "location": {"heading": ["About Us"], "offset": 1234, "end": 1258, "chunk": 0},
    "alternatives": [
      {
        "value": "Acme Group",
        "confidence": 0.7,
        "quote": "the Acme Group portfolio",
        "grounding": 0.95,
        "location": {"heading": ["Home"], "offset": 45, "end": 68, "chunk": 0}
      }
    ]
  },
  "employee_count": {
    "value": 500,
    "confidence": 0.6,
    "quote": "over 500 employees worldwide",
    "grounding": 1.0,
    "location": {"heading": ["Company Facts"], "offset": 8920, "end": 8948, "chunk": 3}
  }
}
```

**Multi-value fields** — list of independently-grounded items:
```json
{
  "certifications": {
    "items": [
      {
        "value": "ISO 9001:2015",
        "confidence": 0.95,
        "quote": "certified to ISO 9001:2015",
        "grounding": 1.0,
        "location": {"heading": ["Quality"], "offset": 5200, "end": 5226, "chunk": 2}
      },
      {
        "value": "ISO 14001",
        "confidence": 0.85,
        "quote": "ISO 14001 environmental",
        "grounding": 1.0,
        "location": {"heading": ["Quality"], "offset": 5280, "end": 5304, "chunk": 2}
      },
      {
        "value": "ATEX",
        "confidence": 0.7,
        "quote": "ATEX certified motors",
        "grounding": 0.9,
        "location": {"heading": ["Products", "Motors"], "offset": 12400, "end": 12421, "chunk": 4}
      }
    ]
  },
  "service_types": {
    "items": [
      {
        "value": "repair",
        "confidence": 0.9,
        "quote": "comprehensive repair services",
        "grounding": 1.0,
        "location": {"heading": ["Services"], "offset": 3100, "end": 3129, "chunk": 1}
      }
    ]
  }
}
```

Each list item is independently grounded and located. "ATEX" found on the products page at offset 12400 has different provenance than "ISO 9001" found on the quality page at 5200. Consolidation can weigh each item independently.

**Entity lists** — each entity is a self-contained unit:
```json
{
  "entities": [
    {
      "fields": {"product_name": "HDP Series", "subcategory": "planetary", "torque_rating_nm": 50000},
      "confidence": 0.9,
      "quote": "HDP Series planetary gearbox",
      "grounding": 1.0,
      "location": {"heading": ["Products", "Planetary Gearboxes"], "offset": 5600, "end": 5628, "chunk": 2}
    },
    {
      "fields": {"product_name": "Cyclo BBB", "subcategory": "cycloidal", "torque_rating_nm": 8000},
      "confidence": 0.75,
      "quote": "the Cyclo BBB series offers",
      "grounding": 0.95,
      "location": {"heading": ["Products", "Cycloidal Drives"], "offset": 9800, "end": 9826, "chunk": 3}
    }
  ]
}
```

### Extraction ORM Changes

```python
class Extraction(Base):
    # ... existing columns stay ...

    # CHANGED: confidence = max of per-field/entity confidences (summary for DB filtering)
    confidence: Mapped[float | None]

    # DEPRECATED: grounding_scores — now embedded in data per item
    grounding_scores: Mapped[dict | None]     # keep for backward compat reads

    # NEW: format version for migration coexistence
    data_version: Mapped[int]                 # 1 = legacy flat, 2 = per-item structured
```

Storage cardinality unchanged: 1 Extraction per (source, field_group).

### Backward Compatibility Utility

```python
def read_field_value(
    data: dict, field_name: str, data_version: int
) -> tuple[Any, float | None, float | None]:
    """Read (value, confidence, grounding) from either v1 or v2 format.

    For v2 single-answer fields, returns the primary value.
    For v2 list fields, returns the list of values (without per-item metadata).
    Single utility for all code that reads Extraction.data.
    """
    if data_version >= 2:
        item = data.get(field_name, {})
        if isinstance(item, dict):
            if "items" in item:
                # Multi-value: return flat list for backward compat
                values = [i["value"] for i in item["items"] if "value" in i]
                return values, None, None
            if "value" in item:
                return item["value"], item.get("confidence"), item.get("grounding")
        return None, None, None
    else:
        return data.get(field_name), None, None
```

---

## Merge Strategy

Merge combines chunk results into one Extraction per (source, field_group). Unlike the current implementation, each item type has a clean, purpose-built merge — and every merged item retains its full provenance.

### Single-Answer Fields: Best Wins + Alternatives

```python
def merge_single_answer(
    field_name: str,
    chunk_items: list[FieldItem],
) -> FieldItem | None:
    """Select best item, preserve alternatives for consolidation visibility.

    Ranking: grounded items always beat ungrounded; within tier, highest
    (grounding * confidence) wins.

    Returns FieldItem with .alternatives populated (non-winning items
    above a minimum quality threshold).
    """
    if not chunk_items:
        return None

    ranked = sorted(
        chunk_items,
        key=lambda i: (
            1 if i.grounding and i.grounding > 0.5 else 0,   # grounded tier
            (i.grounding or 0.0) * i.confidence,               # quality score
        ),
        reverse=True,
    )

    best = ranked[0]

    # Keep alternatives that differ in value (for consolidation context)
    alternatives = []
    seen_values = {_normalize(best.value)}
    for item in ranked[1:]:
        norm = _normalize(item.value)
        if norm not in seen_values and item.confidence >= 0.3:
            alternatives.append(item)
            seen_values.add(norm)

    return replace(best, alternatives=alternatives if alternatives else None)
```

The `alternatives` list gives consolidation visibility into what other chunks said. When 3 chunks say "Acme Corp" and 1 chunk says "Acme Corporation AG", consolidation sees both values with their provenance — not just the winner.

### Boolean Fields: Credible True Wins

```python
def merge_boolean(
    chunk_items: list[FieldItem],
) -> FieldItem | None:
    """Boolean merge: True wins only if credible (grounded or confident).

    Prevents single low-confidence True from irrelevant chunk
    overriding multiple confident False results.
    """
    if not chunk_items:
        return None

    true_items = [i for i in chunk_items if i.value is True]
    false_items = [i for i in chunk_items if i.value is False]

    if not true_items:
        return max(false_items, key=lambda i: i.confidence) if false_items else None

    best_true = max(true_items, key=lambda i: (i.grounding or 0.0, i.confidence))

    # True is credible if grounded OR confident
    if (best_true.grounding and best_true.grounding > 0.5) or best_true.confidence >= 0.5:
        return best_true

    # Low-confidence ungrounded True — prefer confident False
    if false_items:
        best_false = max(false_items, key=lambda i: i.confidence)
        if best_false.confidence > best_true.confidence:
            return best_false

    return best_true
```

### Multi-Value Fields: Union All Items

```python
def merge_list_values(
    field_name: str,
    chunk_lists: list[list[ListValueItem]],
) -> list[ListValueItem]:
    """Union all list items from all chunks. Each item keeps its own provenance.

    Deduplicates by normalized value. When duplicate found, keeps the
    item with better grounding.
    """
    seen: dict[str, int] = {}      # normalized_value -> index in merged
    merged: list[ListValueItem] = []

    for chunk_list in chunk_lists:
        for item in chunk_list:
            norm = _normalize(item.value)
            if norm in seen:
                existing_idx = seen[norm]
                if (item.grounding or 0) > (merged[existing_idx].grounding or 0):
                    merged[existing_idx] = item
            else:
                seen[norm] = len(merged)
                merged.append(item)

    return merged
```

No "pick best list" — every individual item from every chunk is preserved with its own `{confidence, quote, grounding, location}`. If chunk 2 found "ISO 9001" under the Quality heading and chunk 4 found "ATEX" under the Products heading, both are kept with their distinct provenance.

### Summary Fields: Longest-Confident or LLM Synthesis

```python
def merge_summary(
    chunk_items: list[FieldItem],
    chunk_results: list[ChunkExtractionResult],
    strategy: str = "longest_confident",
) -> FieldItem | None:
    """Merge summary field across chunks.

    Two strategies:
    - longest_confident: pick the chunk summary with highest confidence * length_score.
      The chunk with the most relevant content produces the most comprehensive summary.
    - llm_synthesize: combine all partial summaries via an LLM call (async, higher quality).
    """
    if not chunk_items:
        return None

    if strategy == "longest_confident":
        # Score by confidence * normalized length
        max_len = max(len(str(i.value)) for i in chunk_items) or 1
        best = max(
            chunk_items,
            key=lambda i: i.confidence * (len(str(i.value)) / max_len),
        )

        # Build evidence fingerprint from headings of all contributing chunks
        contributing_headings = []
        for cr in chunk_results:
            if cr.items.get(chunk_items[0]) is not None:  # had content for this field
                # Use source_location heading if available
                for item in chunk_items:
                    if item.source_location and item.source_location.heading_path:
                        top_heading = item.source_location.heading_path[0]
                        if top_heading not in contributing_headings:
                            contributing_headings.append(top_heading)

        evidence = f"Based on: {', '.join(contributing_headings)}" if contributing_headings else None

        return FieldItem(
            value=best.value,
            confidence=best.confidence,
            quote=evidence,         # not a verbatim quote — evidence fingerprint
            grounding=None,         # summaries are not grounded
            source_location=best.source_location,
        )

    # llm_synthesize handled in orchestrator (requires async LLM call)
    # This function returns a marker; orchestrator performs the actual synthesis
    return FieldItem(
        value="\n\n".join(str(i.value) for i in chunk_items if i.value),
        confidence=max(i.confidence for i in chunk_items),
        quote=None,
        grounding=None,
    )
```

For `llm_synthesize` strategy, the orchestrator makes one additional LLM call after merge:
```python
if has_summary_fields_needing_synthesis:
    combined_partials = "\n---\n".join(partial_summaries)
    synthesis_prompt = f"Combine these partial descriptions into one coherent summary:\n{combined_partials}"
    synthesized = await self._extractor.synthesize(synthesis_prompt, max_tokens=500)
    merged_data[field_name] = FieldItem(value=synthesized, confidence=..., grounding=None).to_dict()
```

This is the only merge operation that requires an LLM call — all others are pure data.

### Entity Lists: Union All Entities

```python
def merge_entities(
    chunk_entities: list[list[EntityItem]],
    id_field_names: list[str],
) -> list[EntityItem]:
    """Union all entities from all chunks and all iterations.

    Each entity keeps its own provenance. Dedup by ID fields,
    keeping better-grounded version on collision.
    """
    seen_ids: dict[str, int] = {}
    merged: list[EntityItem] = []

    for entities in chunk_entities:
        for entity in entities:
            entity_id = _get_entity_id(entity.fields, id_field_names)

            if entity_id and entity_id in seen_ids:
                existing_idx = seen_ids[entity_id]
                if (entity.grounding or 0) > (merged[existing_idx].grounding or 0):
                    merged[existing_idx] = entity
                continue

            if entity_id:
                seen_ids[entity_id] = len(merged)
            merged.append(entity)

    return merged
```

### Merge Dispatch

```python
def merge_chunk_results(
    chunk_results: list[ChunkExtractionResult],
    group: FieldGroup,
    id_field_names: list[str],
) -> dict:
    """Merge all chunk results into one Extraction.data dict (v2 format).

    Dispatches to cardinality-specific merge per field.
    """
    if group.is_entity_list:
        all_entities = [cr.entities or [] for cr in chunk_results]
        merged_entities = merge_entities(all_entities, id_field_names)
        return {"entities": [e.to_dict() for e in merged_entities]}

    result = {}
    for field in group.fields:
        if field.field_type == "list":
            # Multi-value: collect ListValueItem lists from each chunk
            chunk_lists = [
                cr.list_items.get(field.name, []) for cr in chunk_results
            ]
            items = merge_list_values(field.name, chunk_lists)
            if items:
                result[field.name] = {"items": [i.to_dict() for i in items]}

        elif field.field_type == "boolean":
            # Boolean: credible True wins
            candidates = [
                cr.items[field.name] for cr in chunk_results
                if field.name in cr.items
            ]
            item = merge_boolean(candidates)
            if item:
                result[field.name] = item.to_dict()

        elif field.field_type == "summary":
            # Summary: longest-with-high-confidence or LLM synthesis
            candidates = [
                cr.items[field.name] for cr in chunk_results
                if field.name in cr.items
            ]
            merge_strategy = field.merge_strategy or "longest_confident"
            item = merge_summary(candidates, chunk_results, merge_strategy)
            if item:
                result[field.name] = item.to_dict()

        else:
            # Single-answer: best wins + alternatives
            candidates = [
                cr.items[field.name] for cr in chunk_results
                if field.name in cr.items
            ]
            item = merge_single_answer(field.name, candidates)
            if item:
                result[field.name] = item.to_dict()

    return result
```

### What Merge No Longer Does

Removed entirely:
- `_pick_highest_confidence()` → replaced by `merge_single_answer()`
- `_merge_chunk_results()` → 130 lines of multi-strategy dispatch → ~80 lines of cardinality-based merge
- `_merge_entity_lists()` → replaced by `merge_entities()`
- `_detect_conflicts()` → alternatives list provides this visibility
- `_apply_defaults()` → missing = missing, not False/[]
- Confidence averaging → each item has own confidence; Extraction.confidence = max
- Quote-from-different-chunk → structurally impossible (quote is bound to item)
- Grounding score propagation → grounding is per-item, inseparable from value

---

## Pagination / Iterative Extraction

### Entity Lists: Unbounded Pagination

A product catalog page might list 50-200 products. The LLM can't extract them all in one pass due to output token limits. Pagination must continue until the content is exhausted.

```python
async def _extract_entities_from_chunk(
    self,
    chunk: DocumentChunk,
    group: FieldGroup,
    source_context: str,
    source_content: str,
) -> list[EntityItem]:
    """Extract all entities from one chunk via iterative pagination.

    Continues until:
    1. LLM returns has_more=false
    2. No new entities found in last iteration (convergence)
    3. Schema max_items reached (safety for pathological content)

    No hardcoded iteration limit — pagination is driven by content.
    """
    all_entities: list[EntityItem] = []
    found_ids: set[str] = set()
    max_items = group.max_items  # from schema, e.g., 200. None = no limit
    consecutive_stalls = 0       # track iterations that produced no new entities
    STALL_LIMIT = 2              # stop after N consecutive stalls

    iteration = 0
    while True:
        iteration += 1

        # Build exclusion list from already-found entities
        already_found = [e.fields for e in all_entities] if all_entities else None

        result = await self._extractor.extract_field_group(
            content=chunk.content,
            field_group=group,
            source_context=source_context,
            already_found=already_found,
        )

        parsed = self._parse_entity_response(result, group, chunk.chunk_index)

        # Ground each entity against FULL source
        grounded_entities = []
        for entity in (parsed.entities or []):
            grounding = ground_entity_item(
                entity_fields=entity.fields,
                quote=entity.quote,
                source_content=source_content,
                id_field_names=self._context.entity_id_fields,
            )
            location = locate_in_source(
                quote=entity.quote,
                source_content=source_content,
                chunk_header_path=chunk.header_path,
                chunk_index=chunk.chunk_index,
            )
            grounded_entities.append(
                replace(entity, grounding=grounding, source_location=location)
            )

        # Dedup against already found
        new_entities = []
        for entity in grounded_entities:
            entity_id = _get_entity_id(entity.fields, self._context.entity_id_fields)
            norm_id = entity_id.strip().lower() if entity_id else None
            if norm_id and norm_id in found_ids:
                continue
            if norm_id:
                found_ids.add(norm_id)
            new_entities.append(entity)

        all_entities.extend(new_entities)

        # ── Stop conditions ──

        # 1. LLM says no more
        if not parsed.has_more:
            break

        # 2. Empty response — LLM found nothing at all
        if not parsed.entities:
            break

        # 3. Stall detection — LLM returned entities but all were duplicates
        #    (or returned nothing new). If this happens N times consecutively,
        #    the LLM is stuck in a loop and won't produce new results.
        if not new_entities:
            consecutive_stalls += 1
            if consecutive_stalls >= STALL_LIMIT:
                logger.warning(
                    "entity_pagination_stalled",
                    group=group.name,
                    chunk=chunk.chunk_index,
                    iteration=iteration,
                    total_found=len(all_entities),
                    consecutive_stalls=consecutive_stalls,
                )
                break
        else:
            consecutive_stalls = 0  # reset on any successful extraction

        # 4. Schema-defined cap
        if max_items and len(all_entities) >= max_items:
            break

        logger.info(
            "entity_pagination_continue",
            group=group.name,
            chunk=chunk.chunk_index,
            iteration=iteration,
            found_so_far=len(all_entities),
            new_this_iteration=len(new_entities),
        )

    return all_entities
```

**Stop conditions (in order of evaluation)**:
1. `has_more=false` — LLM says content is exhausted
2. Empty response — LLM returned zero entities (nothing left to find)
3. Stall detection — LLM returned entities but ALL were duplicates of already-found items, N times consecutively (default N=2). This catches the case where the LLM keeps returning "Motor X" despite the exclusion list saying "do not repeat Motor X". One stall is forgiven (LLM might self-correct), two consecutive stalls = stuck in a loop.
4. Schema `max_items` — content-appropriate cap (e.g., 200 for products)
5. Safety cap `ENTITY_PAGINATION_SAFETY_CAP` — absolute maximum (default 500)

No `max_iterations` counter. A page with 100 products iterates ~10 times — driven by content, not an arbitrary limit.

### Exclusion Prompt

```python
if already_found:
    # Show only identifying fields to keep prompt compact
    found_names = []
    for entity in already_found:
        id_value = None
        for id_field in self._context.entity_id_fields:
            if entity.get(id_field):
                id_value = str(entity[id_field])
                break
        if id_value:
            found_names.append(id_value)

    exclusion_text = "\n".join(f"- {name}" for name in found_names)
    prompt += f"""
ALREADY EXTRACTED ({len(found_names)} items — do NOT repeat):
{exclusion_text}

Extract ONLY additional items not listed above.
If no more items exist in this content, return empty list with has_more: false.
"""
```

### Truncation Recovery

When `finish_reason="length"` (LLM ran out of output tokens):

```python
if finish_reason == "length":
    # Try to salvage completed entities from truncated JSON
    repaired = try_repair_json(result_text, context="entity_truncated")
    parsed = self._parse_entity_response(repaired, group, chunk_idx)

    # Truncation implies more content — force pagination
    parsed.has_more = True

    logger.info(
        "entity_extraction_truncated_continuing",
        group=group.name,
        chunk=chunk_idx,
        salvaged_entities=len(parsed.entities or []),
    )
```

Instead of returning empty with `_truncated=True` (current: data loss), we salvage whatever entities were completed and force another iteration. The next pass will pick up where the truncation cut off.

### Multi-Value Fields: No Pagination Needed

For `list` fields like `certifications`, the LLM extracts all items in a single pass per chunk because:
1. List items are simple values (strings), not compound objects — the response is compact
2. A typical chunk won't have 50+ certifications (unlike products)
3. Different chunks contribute different items, and merge unions them

If a list field ever needs pagination (extremely long lists in a single chunk), the entity iteration mechanism can be adapted. But this is unlikely in practice.

### Single-Answer Fields: No Pagination

One answer per chunk by definition. Multiple chunks may produce different answers — resolved by merge, not pagination.

---

## Grounding Pipeline

### Per-Item Complete Grounding

Every extracted item goes through the full grounding chain inline:

```
LLM produces {value, confidence, quote}
  |
  +-- grounding_mode = "none" (text, summary) ---------> grounding = None (not applicable)
  +-- grounding_mode = "semantic" (boolean) -----------> grounding = 1.0 (defer to confidence)
  +-- grounding_mode = "required" (string/int/...) ---+
                                                       |
    quote is None? ------------------------------------> grounding = 0.0 (no evidence)
    quote exists:                                       |
      Layer A: verify_quote_in_source(quote, source) --+
        < 0.5 -> fabricated quote ----------------------> grounding = 0.0
        >= 0.5 -> quote is real                         |
      Layer B: score_field(value, quote, field_type) ---+
        -> value_score                                   |
      grounding = min(A, B)                              |
        = 0.0 AND quote exists? ------------------------> try LLM verify (optional)
          LLM says yes ---------------------------------> grounding = LLM_score
          LLM says no ----------------------------------> grounding = 0.0
```

**Layer A runs against FULL source content**, not chunk. The LLM saw only the chunk, but we verify against the full source. This is more robust because chunks are ephemeral and may have boundary artifacts.

### Grounding Functions

```python
def ground_field_item(
    value: Any,
    quote: str | None,
    source_content: str,         # FULL source, not chunk
    field_type: str,
    grounding_mode: str | None = None,
) -> float:
    """Complete two-layer grounding for one field item."""
    mode = grounding_mode or GROUNDING_DEFAULTS.get(field_type, "required")
    # summary type is always "none" — synthesized content, no grounding
    if mode in ("none", "semantic"):
        return None if field_type == "summary" else 1.0
    if not quote:
        return 0.0

    quote_in_source = verify_quote_in_source(quote, source_content)
    if quote_in_source < 0.5:
        return 0.0

    value_in_quote = score_field(value, quote, field_type)
    return round(min(quote_in_source, value_in_quote), 4)


def ground_entity_item(
    entity_fields: dict[str, Any],
    quote: str | None,
    source_content: str,
    id_field_names: list[str],
) -> float:
    """Ground an entity: does this entity exist in the source?

    Checks entity identity (name/ID) against quote and source.
    Does NOT ground individual attributes — entity existence is what matters.
    """
    if not quote:
        return 0.0

    quote_in_source = verify_quote_in_source(quote, source_content)
    if quote_in_source < 0.5:
        return 0.0

    identity_value = None
    for id_field in id_field_names:
        if id_field in entity_fields and entity_fields[id_field]:
            identity_value = entity_fields[id_field]
            break

    if identity_value is None:
        return quote_in_source

    value_in_quote = verify_string_in_quote(str(identity_value), quote)
    return round(min(quote_in_source, value_in_quote), 4)
```

### Multi-Value Item Grounding

Each item in a list field is grounded independently:

```python
# In orchestrator, after parsing chunk response for a list field
for list_item in chunk_list_items:
    list_item.grounding = ground_field_item(
        value=list_item.value,
        quote=list_item.quote,
        source_content=source_content,
        field_type="string",  # list items are individual values
    )
    list_item.source_location = locate_in_source(...)
```

"ISO 9001" gets grounded independently from "ATEX". If the LLM hallucinated one certification but correctly extracted another, the hallucinated one gets grounding=0.0 while the correct one gets 1.0. Consolidation can then drop the hallucinated item.

### Selective LLM Grounding (Optional, Bounded)

After string-match grounding, for items where grounding=0.0 but a quote exists:

```python
MAX_LLM_GROUNDING_PER_SOURCE = 5

ungrounded_items = collect_ungrounded_items(all_chunk_results)

if llm_verifier and len(ungrounded_items) <= MAX_LLM_GROUNDING_PER_SOURCE:
    for item_ref in ungrounded_items:
        llm_score = await llm_verifier.verify_single(item_ref.value, item_ref.quote)
        if llm_score > 0:
            item_ref.grounding = llm_score
```

Bounded per source (not per chunk) to limit total cost. Opt-in via config.

### Source-Grounding Retry (Revised)

Re-extract a chunk only if **majority of required fields have fabricated quotes** (grounding=0.0 AND quote exists). Missing quotes are a prompt-following failure, not fabrication.

```python
required_items = [
    item for name, item in chunk_items.items()
    if field_lookup[name].grounding_mode_resolved == "required"
    and item.value is not None
]
fabricated = [item for item in required_items if item.quote and item.grounding == 0.0]

if required_items and len(fabricated) / len(required_items) > 0.5:
    retry_result = await extractor.extract_field_group(..., strict_quoting=True)
    # Re-ground, keep whichever result has more grounded items
```

---

## LLM Prompt Format

### Field Group Prompt (non-entity)

```
You are extracting {description} from {source_type}.

For each field, provide the value, your confidence (0.0-1.0), and an exact
verbatim quote (15-50 chars) from the source that supports the value.

Fields to extract:
- "company_name" (text): Official company name [REQUIRED]
- "employee_count" (integer): Number of employees
- "certifications" (list): ISO certifications, industry standards
- "company_description" (summary): Brief description of the company

{prompt_hint}

RULES:
- Extract ONLY from the content below. Do NOT use outside knowledge.
- Confidence is PER FIELD: 0.0=no info, 0.5=ambiguous, 0.9=clear evidence
- For list fields: extract ALL items found, each with its own quote
- For summary fields: write a concise synthesis based on the content. No quote needed.
  Confidence reflects how much relevant material was available.
- Omit fields entirely if no information found (do not include with null)
- Quote must be EXACT text copied from the source (except for summary fields)

Output JSON:
{
  "fields": {
    "company_name": {"value": "...", "confidence": 0.9, "quote": "exact text..."},
    "employee_count": {"value": 500, "confidence": 0.6, "quote": "about 500 emp..."},
    "certifications": {
      "items": [
        {"value": "ISO 9001", "confidence": 0.9, "quote": "certified to ISO 9001"},
        {"value": "CE", "confidence": 0.7, "quote": "CE marked products"}
      ]
    },
    "company_description": {"value": "Acme Corp manufactures...", "confidence": 0.8}
  }
}
```

Note: list fields use `"items": [...]` format. Summary fields have no quote — they are synthesized, not extracted verbatim.

### Entity List Prompt

```
You are extracting {description} from {source_type}.

For each {entity_singular} found, extract its attributes as a unit.
Provide a confidence score and a verbatim quote (15-50 chars) identifying
EACH entity in the source text.

Fields per entity:
- "product_name" (text): Product name [REQUIRED]
- "subcategory" (text): planetary, helical, worm, bevel, cycloidal
- "torque_rating_nm" (float): Torque rating in Nm

{prompt_hint}

RULES:
- Extract ONLY from content below. Do NOT use outside knowledge.
- Each entity is a unit — extract all attributes together.
- Confidence: how certain this entity actually exists (0.0-1.0)
- Quote: exact text that identifies THIS entity in the source
- has_more: true if you believe MORE entities exist in this content
  beyond what you've extracted here

Output JSON:
{
  "entities": [
    {
      "fields": {"product_name": "...", "subcategory": "...", "torque_rating_nm": ...},
      "confidence": 0.9,
      "quote": "exact text identifying this entity"
    }
  ],
  "has_more": true
}
```

### Entity Pagination Prompt (iteration > 1)

Adds exclusion section:

```
ALREADY EXTRACTED (47 items — do NOT repeat):
- HDP Series
- Cyclo BBB
- Paramax 9000
...

Extract ONLY additional {entity_singular} items not listed above.
If no more items exist in this content, return empty entities list with has_more: false.
```

---

## Implementation Phases

### Phase 1: Per-Field Structured Response + Data Model

**Goal**: LLM returns per-field `{value, confidence, quote}` with cardinality-aware format. Zero extra LLM calls. Backward compatible.

**Files changed**:
- New: `src/services/extraction/extraction_items.py` — FieldItem, ListValueItem, EntityItem, SourceLocation, ChunkExtractionResult, read_field_value()
- `src/services/extraction/schema_extractor.py` — new prompt format, response parsing
- `src/services/extraction/field_groups.py` — no changes needed

#### 1A. Data Model

`extraction_items.py`: All item types, ChunkExtractionResult, serialization, backward-compat reader.

#### 1B. Prompt Format

`schema_extractor.py:_build_system_prompt()`: Cardinality-aware format:
- Single-answer fields: `{"value": ..., "confidence": ..., "quote": ...}`
- List fields: `{"items": [{"value": ..., "confidence": ..., "quote": ...}]}`
- Entity lists: `{"entities": [{"fields": {...}, "confidence": ..., "quote": ...}], "has_more": ...}`

#### 1C. Response Parsing

`_parse_structured_response(raw, field_group, chunk_index) -> ChunkExtractionResult`:
- Routes each field to correct item type based on `FieldDefinition.field_type`
- Fallback: if response is flat format (no `"fields"` wrapper), convert to v2 with per-field confidence = overall confidence
- List field response: parses `"items"` array into `list[ListValueItem]`

#### 1D. Source Location

`locate_in_source()`, `_find_quote_position()`, `_heading_at_position()` in extraction_items.py.

---

### Phase 2: Inline Complete Grounding

**Goal**: Every item gets full two-layer grounding during extraction.

**Files changed**:
- `src/services/extraction/grounding.py` — `ground_field_item()`, `ground_entity_item()`
- `src/services/extraction/schema_orchestrator.py` — grounding + location in extraction loop

#### 2A. Grounding Functions

`ground_field_item()` and `ground_entity_item()` in grounding.py (see above).

#### 2B. Orchestrator Integration

After parsing each chunk's LLM response:
1. For each FieldItem: `ground_field_item()` + `locate_in_source()`
2. For each ListValueItem: same, per individual list item
3. For each EntityItem: `ground_entity_item()` + `locate_in_source()`

All grounding against FULL source content (passed as `markdown` parameter to `extract_all_groups()`).

#### 2C. Selective LLM Grounding (Optional, defer-able)

Opt-in. Bounded per source. See grounding section.

#### 2D. Revised Source-Grounding Retry

Per-field fabrication detection replaces ratio-based retry.

---

### Phase 3: Merge Rewrite + Storage

**Goal**: Replace lossy multi-strategy merge with cardinality-based merge. Store v2 format.

**Files changed**:
- New or in orchestrator: `merge_single_answer()`, `merge_boolean()`, `merge_list_values()`, `merge_entities()`, `merge_chunk_results()`
- `src/services/extraction/pipeline.py` — store v2 format
- `src/services/extraction/consolidation.py` — read v2 format, per-field weights
- `src/services/extraction/consolidation_service.py` — pass data_version
- DB migration: add `data_version` column

#### 3A. Merge Module

~80 lines replacing ~250 lines of current merge + helpers.

#### 3B. Pipeline Storage

```python
extraction = Extraction(
    project_id=source.project_id,
    source_id=source.id,
    data=merged_result,              # v2 per-item structured format
    data_version=2,
    extraction_type=group.name,
    source_group=context_value,
    confidence=summary_confidence,   # max of per-field confidences
    profile_used=schema_name,
)
```

#### 3C. DB Migration

`ALTER TABLE extractions ADD COLUMN data_version INTEGER NOT NULL DEFAULT 1;`

#### 3D. Consolidation Adapts

For v2 data, consolidation uses per-field confidence and per-field grounding:

```python
# Single-answer field
item = data.get(field_name, {})
value = item.get("value")
weight = effective_weight(item.get("confidence", 0.5), item.get("grounding"), mode)

# Multi-value field: each list item is an independent WeightedValue
for list_item in data.get(field_name, {}).get("items", []):
    value = list_item.get("value")
    weight = effective_weight(list_item.get("confidence", 0.5), list_item.get("grounding"), mode)
    weighted_values.append(WeightedValue(value, weight, source_id))
```

Multi-value consolidation: each individual list item from each extraction contributes independently. "ISO 9001" with grounding=1.0 from source A and "ISO 9001" with grounding=0.9 from source B both contribute as weighted votes. Consolidation's `union_dedup` merges them, keeping the best-weighted version.

---

### Phase 4: Iterative Entity Extraction

**Goal**: Extract entities with unbounded pagination via `has_more` signal.

**Files changed**:
- `src/services/extraction/schema_extractor.py` — exclusion prompt, has_more in response format, truncation recovery
- `src/services/extraction/schema_orchestrator.py` — pagination loop per chunk for entity groups

#### 4A. Pagination Loop

`_extract_entities_from_chunk()` — see Pagination section above. Stops on: has_more=false, convergence, or max_items.

#### 4B. Exclusion Prompt

Compact exclusion list showing only entity ID/name values.

#### 4C. Truncation Recovery

`finish_reason="length"` → salvage completed entities, force has_more=True, continue iteration.

---

### Phase 5: Consolidation Strategy Fixes

**Goal**: Fix remaining quality issues with full per-field metadata available.

#### 5A. Default Strategies

```python
STRATEGY_DEFAULTS["string"] = "weighted_frequency"   # was "frequency"
STRATEGY_DEFAULTS["enum"] = "weighted_frequency"      # was "frequency"
```

#### 5B. Fix grounded_count

Add `grounding_score` to WeightedValue. Count extractions with grounding > 0.5 instead of weight > 0.

#### 5C. Boolean Consolidation

Already correct — `any_true(min_count=3)` works well when each boolean value has accurate per-field confidence (from Phase 1) and the merge no longer inflates True (from Phase 3).

---

## Phase Dependencies

```
Phase 1A: Data model (FieldItem, ListValueItem, EntityItem, SourceLocation)
    +-> Phase 1B: New LLM prompt format
    |   +-> Phase 1C: Response parsing (cardinality-aware)
    +-> Phase 1D: Source location computation
    |
    +-> Phase 2A: ground_field_item() / ground_entity_item()
        +-> Phase 2B: Inline grounding in orchestrator (uses 1C + 1D + 2A)
            +-> Phase 2C: Selective LLM grounding (optional, can defer)
            +-> Phase 2D: Revised retry logic
            |
            +-> Phase 3A: Cardinality-based merge (uses items from 1A, grounding from 2B)
                +-> Phase 3B: Pipeline storage (v2 format)
                    +-> Phase 3C: DB migration
                        +-> Phase 3D: Consolidation reads v2
                            +-> Phase 5A-C: Strategy fixes

Phase 4A-C: Iterative entity extraction (independent, needs 1A + 1B + 2A)
```

**Critical path**: 1A -> 1B -> 1C -> 1D -> 2A -> 2B -> 3A -> 3B -> 3C -> 3D

**Can be deferred**: 2C (LLM grounding), 4 (entity pagination), 5 (strategy fixes)

---

## Risk Analysis

### LLM Response Format

Small LLMs may struggle with the cardinality-aware nested format.

**Mitigation**:
1. Test with Qwen3-30B and gemma3-12b before committing
2. Clear examples in prompt for each field type
3. **Fallback parser**: flat response auto-converted to v2
4. v2 format is arguably simpler — list fields explicitly ask for `items: [...]` instead of ambiguous flat list

### Entity Pagination Cost

Unbounded iteration could be expensive for pages with very many entities.

**Mitigation**:
1. Schema `max_items` provides content-appropriate cap (set to 200 for product catalogs, 50 for services)
2. Convergence detection stops iteration when LLM repeats itself
3. Each iteration is ~0.5s for Qwen3-30B — 10 iterations = 5s extra per chunk, acceptable
4. Monitor: log iteration count per source for tuning max_items
5. If `max_items` is None (unset), apply a safety default (500) to prevent runaway loops

### Source Location Computation

O(n) string search per item per chunk on full source.

**Mitigation**:
- Sources avg 20-50KB. <1ms per search. Negligible vs LLM latency.
- Precompute normalized source content once per source extraction

### Multi-Value Grounding Explosion

A list field with 15 items × 5 chunks = 75 grounding checks.

**Mitigation**:
- Each `ground_field_item()` call is pure string matching — microseconds, not LLM calls
- LLM grounding (optional) is bounded per source, not per item

---

## Testing Strategy

### Per Phase

**Phase 1**:
- FieldItem/ListValueItem/EntityItem serialization roundtrip
- Prompt generates correct format for each field type (single, list, entity)
- Parser handles: structured response, flat fallback, missing fields, missing confidence
- read_field_value works for v1 and v2, both single and list formats
- ChunkExtractionResult correctly classifies field types

**Phase 2**:
- ground_field_item: all (quote/no-quote, real/fabricated, value-match/mismatch) × (string, int, float, bool, list, text)
- ground_entity_item: entity exists vs hallucinated
- locate_in_source: quote found at correct offset, heading derived correctly
- Integration: chunk extraction + grounding produces per-item scores

**Phase 3**:
- merge_single_answer: best-grounded wins, alternatives preserved
- merge_boolean: credible True wins, low-confidence True loses to confident False
- merge_list_values: union + dedup, better-grounded version kept per item
- merge_entities: union + dedup by ID, better-grounded kept
- Pipeline stores v2 format with correct data_version
- Consolidation reads v2: per-field confidence * per-field grounding for weights

**Phase 4**:
- Pagination: continues until has_more=false or convergence
- Exclusion prompt prevents duplicates
- Truncation triggers continuation, salvaged entities kept
- Dedup between iterations works correctly
- No infinite loops (convergence detection)

**Phase 5**:
- weighted_frequency beats frequency for grounded-vs-ungrounded scenario
- grounded_count reflects actual grounding (not always = source_count)

### Integration Test

End-to-end on known sources:
1. Extract → verify per-field confidence varies across fields
2. List fields have per-item grounding (not one score for whole list)
3. Entity pagination extracts all products from a catalog page
4. source_location points to correct heading/offset
5. Re-extract with different chunk parameters → same format, locations still valid
6. Consolidate v2 → compare quality to v1

---

## Configuration

New config entries on ExtractionConfig facade:

```python
# Phase 2
INLINE_VALUE_GROUNDING: bool = True           # value-in-quote during extraction
INLINE_LLM_GROUNDING: bool = False            # LLM verification inline (opt-in)
MAX_LLM_GROUNDING_PER_SOURCE: int = 5         # cap LLM grounding calls per source

# Phase 4
ENTITY_ITERATION_ENABLED: bool = True         # iterative entity extraction
ENTITY_PAGINATION_SAFETY_CAP: int = 500       # absolute max entities per chunk (prevents runaway)
ENTITY_PAGINATION_STALL_LIMIT: int = 2        # stop after N consecutive all-duplicate iterations
```

Note: no `ENTITY_MAX_ITERATIONS` — iteration is driven by content, not a counter. Safety is via `max_items` on the schema + `ENTITY_PAGINATION_SAFETY_CAP`.

---

## What Gets Removed (After v2 Validated)

1. `_merge_chunk_results()` — replaced by cardinality-based merge (~250 → ~80 lines)
2. `_merge_entity_lists()` — replaced by `merge_entities()`
3. `_pick_highest_confidence()` — absorbed into `merge_single_answer()`
4. `_detect_conflicts()` — alternatives list provides this visibility
5. `_apply_defaults()` — missing = missing, not False/[]
6. `_is_empty_result()` — per-field confidence makes this unnecessary
7. `_source_grounding_ratio()` / `_collect_quotes()` — replaced by per-item grounding
8. `compute_chunk_grounding()` / `compute_chunk_grounding_entities()` — replaced by `ground_field_item()` / `ground_entity_item()`
9. Separate `grounding_scores` column writes — embedded in data
10. Backfill script for grounding — grounding is inline
11. Confidence averaging — each item has own confidence
