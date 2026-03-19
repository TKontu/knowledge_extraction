# Pipeline Review: Report Generation

**Date**: 2026-03-02
**Scope**: Report synthesis, table generation, output formats (Markdown, XLSX, PDF)

---

## 1. Overview

The report pipeline synthesizes extracted knowledge into human-readable reports. It supports three report types and multiple output formats.

```
Extractions (DB) → Query & Filter → Synthesis (LLM) → Format → Output
```

---

## 2. Report Types

| Type | Purpose | LLM Usage |
|------|---------|-----------|
| **SINGLE** | Summarize findings for one company/source group | Yes - narrative synthesis |
| **COMPARISON** | Compare findings across multiple companies | Yes - comparative analysis |
| **TABLE** | Structured tabular format with all field groups | Optional - domain merge only |

---

## 3. Report Generation Flow

### 3.1 Request Processing

```
POST /api/v1/projects/{project_id}/reports
  → Validate project exists
  → Query extractions (filtered by source_groups, entity_types, categories)
  → Route to appropriate generator (single/comparison/table)
  → Store report in DB
  → Return report content
```

### 3.2 Single Report

Generates a narrative summary for one company:

1. Query all extractions for the source group
2. Group by extraction type / field group
3. Synthesize each group via LLM (two-pass for large datasets)
4. Combine into coherent narrative with source attribution
5. Output as Markdown

### 3.3 Comparison Report

Compares multiple companies side-by-side:

1. Query extractions for all specified source groups
2. Group by field group, then by company
3. LLM comparison synthesis per field group
4. Highlight differences, similarities, conflicts
5. Output as Markdown with comparison sections

### 3.4 Table Report

Structured data output with two grouping modes:

**Grouping: `source`** (default)
- One row per URL/source
- All field group extractions flattened into columns
- Direct data mapping, no LLM

**Grouping: `domain`**
- One row per domain
- LLM smart merge synthesizes values from all URLs within domain
- Conflict resolution for disagreeing values
- Optional `include_merge_metadata` for audit trail

---

## 4. LLM Synthesis (`src/services/reports/synthesis.py`)

### ReportSynthesizer

**Two-pass synthesis for large datasets**:

**Pass 1**: Synthesize chunks independently
- Max 15 facts per synthesis call
- Each chunk produces a partial narrative

**Pass 2**: Unify chunk results
- Combine partial narratives into coherent whole
- Resolve cross-chunk conflicts
- Deduplicate overlapping information

### Synthesis Output

```json
{
  "synthesized_text": "Combined fact with [Source: title] attribution",
  "sources_used": ["uri1", "uri2"],
  "confidence": 0.85,
  "conflicts_noted": ["Any contradictions found"]
}
```

### Synthesis Modes

| Mode | When Used | Behavior |
|------|-----------|----------|
| `summarize` | Single reports | Narrative summary |
| `compare` | Comparison reports | Side-by-side analysis |
| `aggregate` | Table domain merge | Value consolidation |

### Error Handling

- Falls back to simple merging if LLM synthesis fails
- Preserves source attribution in fallback mode
- Logs synthesis failures for observability

---

## 5. Output Formats

### Markdown (default)

Standard markdown with:
- Headers for sections/field groups
- Source attribution links
- Confidence indicators
- Conflict notes

### XLSX (Excel)

Via `openpyxl`:
- One sheet per report section
- Headers as first row
- Data cells with formatting
- Streaming for large datasets

### PDF

Generated from markdown:
- Rendered via HTML intermediate
- Basic formatting preserved
- Available via `/reports/{id}/pdf` endpoint

---

## 6. Configuration

```python
# Report limits
max_extractions = 50       # Default per report request (1-200)
max_detail_extractions = 10 # Default for detailed sections (1-100)

# Columns (table reports)
columns = None             # Auto-detect from schema, or specify explicitly

# Format
output_format = "md"       # "md" or "xlsx"

# Grouping (table only)
group_by = "source"        # "source" or "domain"
include_merge_metadata = False  # Domain merge audit trail
```

---

## 7. Data Flow Diagram

```
                     ┌──────────────────┐
                     │  Report Request   │
                     │  (type, groups,   │
                     │   format)         │
                     └────────┬─────────┘
                              │
                     ┌────────▼─────────┐
                     │ Query Extractions │
                     │ (by source_group, │
                     │  type, confidence)│
                     └────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
       ┌──────▼──────┐ ┌─────▼─────┐ ┌──────▼──────┐
       │   SINGLE    │ │ COMPARE   │ │   TABLE     │
       │             │ │           │ │             │
       │ Group by    │ │ Group by  │ │ group_by:   │
       │ field group │ │ company + │ │  source OR  │
       │             │ │ field grp │ │  domain     │
       │ LLM synth   │ │ LLM comp │ │ [LLM merge] │
       │ (2-pass)    │ │ (2-pass) │ │             │
       └──────┬──────┘ └─────┬─────┘ └──────┬──────┘
              │               │               │
              └───────────────┼───────────────┘
                              │
                     ┌────────▼─────────┐
                     │   Format Output   │
                     │  MD / XLSX / PDF   │
                     └────────┬─────────┘
                              │
                     ┌────────▼─────────┐
                     │  Store Report     │
                     │  (DB record)      │
                     └──────────────────┘
```

---

## 8. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Two-pass synthesis | LLM context limits require chunking; second pass unifies |
| Source attribution | Traceability back to original pages |
| Domain merge as opt-in | Adds LLM cost and latency; most users want per-source data |
| Fallback to simple merge | Report generation shouldn't fail if LLM is unavailable |
| Conflict detection in synthesis | Same field from different sources may disagree |
| Max 50 extractions default | Bounds LLM costs; adjustable per request |
