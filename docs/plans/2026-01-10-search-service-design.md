# SearchService Design

**Date:** 2026-01-10
**Status:** Implemented
**Related:** TODO_storage.md

## Overview

SearchService combines semantic search (Qdrant vector database) with structured filtering (PostgreSQL JSONB queries) to enable hybrid search over extracted knowledge.

## Architecture

### Dependencies

- **EmbeddingService**: Generates query embeddings via BGE-large-en (1024 dimensions)
- **QdrantRepository**: Performs vector similarity search
- **ExtractionRepository**: Applies JSONB filters and provides database access

### Data Model

```python
@dataclass
class ExtractionSearchResult:
    extraction_id: UUID
    score: float              # Cosine similarity score from Qdrant (0.0-1.0)
    data: dict                # Full JSONB extraction data
    source_group: str
    source_uri: str
    confidence: float | None  # Extraction confidence score
```

## Search Algorithm

### 4-Step Process

1. **Generate Query Embedding**
   - Convert natural language query to 1024-dim vector using EmbeddingService

2. **Over-Fetch from Qdrant**
   - Fetch `limit * 2` results (over-fetch strategy)
   - Apply basic filters in Qdrant payload:
     - `project_id` (always applied)
     - `source_group` (optional list filter)
   - Returns scored results ordered by similarity

3. **Apply PostgreSQL JSONB Filters**
   - If `jsonb_filters` provided, query ExtractionRepository.filter_by_data()
   - Build set of valid extraction IDs
   - Filter Qdrant results to only include matches
   - Early exit if no results remain

4. **Enrich and Trim**
   - Fetch full Extraction records for top `limit` results
   - Join with Source table to get URIs
   - Build ExtractionSearchResult objects
   - Maintain score ordering from Qdrant

### Over-Fetch Strategy Rationale

Fetching `limit * 2` results before filtering ensures:
- Sufficient results remain after JSONB filtering
- Minimal Qdrant queries (single search call)
- Balance between efficiency and result quality

## Design Decisions

### 1. Full Enrichment (Not Minimal Payload)

**Chosen:** Always fetch complete Extraction data + Source URIs
**Alternative:** Return only Qdrant payload fields

**Rationale:**
- Single additional batch query is negligible overhead
- Returns immediately usable data matching TODO spec
- Avoids forcing callers to make additional queries

### 2. No filter_only() Method

**Chosen:** Users call ExtractionRepository directly for non-semantic queries
**Alternative:** Include filter_only() pass-through method

**Rationale:**
- Follows single responsibility principle
- SearchService focuses on hybrid search
- Avoids redundant API surface

### 3. Over-Fetch Before Filter (Not Smart Multiplier)

**Chosen:** Fixed 2x over-fetch multiplier
**Alternative:** Configurable multiplier or pre-filtering in Qdrant

**Rationale:**
- Simplicity - no configuration needed
- Qdrant payload filters can't handle complex JSONB queries
- 2x multiplier provides good balance in practice

## Error Handling

- **Embedding failures:** Propagate (EmbeddingService has retry logic)
- **Qdrant errors:** Propagate to caller
- **Database errors:** Propagate to caller
- **Missing extractions:** Skip silently (defensive)
- **Missing sources:** Return empty string for source_uri (defensive)

## Testing

**Coverage:** 14 unit tests with mocks

Key test scenarios:
- Query embedding generation
- Over-fetch factor (limit * 2)
- Project ID filtering (always applied)
- Source groups filtering (optional)
- JSONB filtering integration
- Result trimming to limit
- Score ordering preservation
- Empty result handling
- Missing data defensive handling
- Full data enrichment

## Files

- Implementation: `pipeline/services/storage/search.py`
- Tests: `pipeline/tests/test_search_service.py`

## Usage Example

```python
from services.storage.search import SearchService

# Initialize with dependencies
search_service = SearchService(
    embedding_service=embedding_service,
    qdrant_repo=qdrant_repo,
    extraction_repo=extraction_repo,
)

# Semantic search with filters
results = await search_service.search(
    project_id=project_id,
    query="API rate limits and pricing",
    limit=20,
    source_groups=["Acme Corp"],
    jsonb_filters={
        "category": "pricing",
        "verified": True,
    },
)

for result in results:
    print(f"Score: {result.score:.2f}")
    print(f"Data: {result.data}")
    print(f"Source: {result.source_uri}")
```

## Future Enhancements

- Configurable over-fetch multiplier
- Pagination support
- Query result caching
- Highlight matching text snippets
- Re-ranking with cross-encoder models
