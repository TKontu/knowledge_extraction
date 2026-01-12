# TODO: Deduplication Strategy

## Overview

Prevent duplicate facts using embedding similarity before storage.

## Status: COMPLETE

**ExtractionDeduplicator fully implemented and integrated into pipeline**

**Completed:**
- ✅ Qdrant client available (`src/qdrant_connection.py`)
- ✅ Embedding endpoint configured (BGE-large-en at `192.168.0.136:9003`)
- ✅ **EmbeddingService** (`services/storage/embedding.py` - 7 tests)
- ✅ **ExtractionDeduplicator** (`services/storage/deduplication.py` - 17 tests)
  - `check_duplicate()` - embedding similarity check with configurable threshold
  - `get_text_from_extraction_data()` - extract text from extraction data dict
  - `check_extraction_data()` - convenience wrapper
  - Default threshold: 0.90 (conservative)
  - Scoped by project_id and source_group
- ✅ **Integrated into ExtractionPipelineService** (`services/extraction/pipeline.py`)

---

## Design Decisions

### MVP: Same-Company Only

**Avoid:** Complex multi-threshold systems (0.90, 0.92, 0.95)
**Instead:** Single threshold (0.90), same-company deduplication only

```python
# Simple: Check Qdrant before insert
similar = await qdrant.search(
    embedding,
    filter={"company": company},
    score_threshold=0.90,
    limit=1,
)
if similar:
    logger.info(f"Skipping duplicate (similar to {similar[0].id})")
    return None  # Don't insert
```

### Skip vs. Merge

**Avoid:** Complex merging logic (keep longest, keep newest, etc.)
**Instead:** Skip duplicates entirely for MVP

Rationale:
- Simpler to implement
- Easier to debug
- Can always add merging later
- First extraction is usually good enough

### No Cross-Company Linking (MVP)

**Avoid:** Separate `fact_links` table, bidirectional relationships
**Instead:** Compute cross-company similarities on-demand for comparison reports

Rationale:
- Adds schema complexity
- Marginal benefit for MVP
- Report generation can do similarity search at query time

---

## Implementation Tasks

### 1. Embedding Service

```python
# services/embedding/client.py
from openai import AsyncOpenAI

class EmbeddingService:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            base_url=settings.openai_embedding_base_url,
            api_key=settings.openai_api_key,
        )
        self.model = settings.rag_embedding_model  # bge-large-en
        self.dimension = 1024

    async def embed(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [item.embedding for item in response.data]
```

### 2. Fact Deduplicator

```python
# services/deduplication/deduplicator.py
from dataclasses import dataclass
from uuid import UUID

@dataclass
class DuplicateCheck:
    is_duplicate: bool
    similar_fact_id: UUID | None = None
    similarity: float | None = None


class FactDeduplicator:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        threshold: float = 0.90,
    ):
        self.qdrant = qdrant_client
        self.embedding = embedding_service
        self.threshold = threshold
        self.collection = "scristill"

    async def check_duplicate(
        self,
        fact_text: str,
        company: str,
    ) -> DuplicateCheck:
        """Check if similar fact exists for this company."""
        # Generate embedding for new fact
        embedding = await self.embedding.embed(fact_text)

        # Search Qdrant for similar facts (same company only)
        results = self.qdrant.search(
            collection_name=self.collection,
            query_vector=embedding,
            query_filter={
                "must": [
                    {"key": "company", "match": {"value": company}}
                ]
            },
            score_threshold=self.threshold,
            limit=1,
        )

        if results:
            return DuplicateCheck(
                is_duplicate=True,
                similar_fact_id=UUID(results[0].payload["fact_id"]),
                similarity=results[0].score,
            )

        return DuplicateCheck(is_duplicate=False)

    async def store_fact_if_unique(
        self,
        fact: ExtractedFact,
        page_id: UUID,
        company: str,
        db: AsyncSession,
    ) -> StoredFact | None:
        """Store fact only if not a duplicate."""
        check = await self.check_duplicate(fact.fact, company)

        if check.is_duplicate:
            logger.info(
                "duplicate_skipped",
                fact=fact.fact[:50],
                similar_to=str(check.similar_fact_id),
                similarity=check.similarity,
            )
            return None

        # Generate embedding and store
        embedding = await self.embedding.embed(fact.fact)

        # Store in PostgreSQL
        stored = Fact(
            page_id=page_id,
            fact_text=fact.fact,
            category=fact.category,
            confidence=fact.confidence,
            profile_used=fact.profile_used,
            metadata={"source_quote": fact.source_quote},
        )
        db.add(stored)
        await db.flush()

        # Store in Qdrant
        self.qdrant.upsert(
            collection_name=self.collection,
            points=[{
                "id": str(stored.id),
                "vector": embedding,
                "payload": {
                    "fact_id": str(stored.id),
                    "fact_text": fact.fact,
                    "company": company,
                    "category": fact.category,
                    "confidence": fact.confidence,
                },
            }],
        )

        return stored
```

### 3. Qdrant Collection Setup

```python
# services/deduplication/setup.py
from qdrant_client.models import Distance, VectorParams

async def init_qdrant_collection(client: QdrantClient):
    """Create Qdrant collection if not exists."""
    collections = client.get_collections().collections
    exists = any(c.name == "scristill" for c in collections)

    if not exists:
        client.create_collection(
            collection_name="scristill",
            vectors_config=VectorParams(
                size=1024,  # BGE-large-en dimension
                distance=Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection 'scristill'")
```

### 4. Integration with Extraction Pipeline

```python
# services/extraction/service.py
class ExtractionService:
    def __init__(
        self,
        llm: LLMClient,
        deduplicator: FactDeduplicator,
        db: AsyncSession,
    ):
        self.llm = llm
        self.deduplicator = deduplicator
        self.db = db

    async def extract_and_store(
        self,
        page: Page,
        profile: ExtractionProfile,
    ) -> ExtractionResult:
        """Extract facts from page and store unique ones."""
        facts = await self.llm.extract_facts(page.markdown_content, profile)

        stored_count = 0
        skipped_count = 0

        for fact in facts:
            stored = await self.deduplicator.store_fact_if_unique(
                fact=fact,
                page_id=page.id,
                company=page.company,
                db=self.db,
            )
            if stored:
                stored_count += 1
            else:
                skipped_count += 1

        await self.db.commit()

        return ExtractionResult(
            page_id=page.id,
            facts_extracted=len(facts),
            facts_stored=stored_count,
            facts_skipped=skipped_count,
        )
```

---

## Configuration

```yaml
deduplication:
  enabled: true
  threshold: 0.90              # Similarity threshold (0.0-1.0)
  same_company_only: true      # MVP: only dedupe within company
  qdrant_collection: scristill
  embedding_dimension: 1024    # BGE-large-en
```

---

## File Structure

```
src/
├── services/
│   ├── embedding/
│   │   ├── __init__.py
│   │   └── client.py          # EmbeddingService
│   └── deduplication/
│       ├── __init__.py
│       ├── deduplicator.py    # FactDeduplicator
│       └── setup.py           # Qdrant collection init
```

---

## Future Enhancements (Post-MVP)

When needed, can add:

1. **Cross-Company Linking:**
   ```sql
   CREATE TABLE fact_links (
       fact_id_a UUID REFERENCES facts(id),
       fact_id_b UUID REFERENCES facts(id),
       similarity FLOAT,
       UNIQUE(fact_id_a, fact_id_b)
   );
   ```

2. **Merge Instead of Skip:**
   ```python
   if check.is_duplicate:
       existing = await fact_repo.get(check.similar_fact_id)
       if fact.confidence > existing.confidence:
           await fact_repo.update(existing.id, confidence=fact.confidence)
   ```

3. **Batch Deduplication:**
   For cleaning up existing data after threshold tuning.

---

## Tasks

- [x] Create `EmbeddingService` class (`services/storage/embedding.py`)
- [x] Create `ExtractionDeduplicator` class (`services/storage/deduplication.py`)
- [x] Qdrant collection initialization (via `QdrantRepository`)
- [x] **Integrated into ExtractionPipelineService** (`services/extraction/pipeline.py`)
- [x] Configuration loading (threshold configurable in constructor)
- [x] Test with sample data (17 tests)
- [ ] Tune threshold with real data (may need adjustment from 0.90)

---

## Testing Checklist

- [x] Unit: Embedding service returns correct dimension (1024)
- [x] Unit: Deduplicator detects exact duplicates (similarity >= threshold)
- [x] Unit: Deduplicator detects near-duplicates above threshold
- [x] Unit: Different content below threshold is NOT flagged
- [x] Unit: Deduplicator scoped by project_id (different project = not duplicate)
- [x] Unit: Deduplicator scoped by source_group (different group = not duplicate)
- [x] Unit: Text extraction from extraction data dict
- [x] Integration: Qdrant collection created on startup
- [x] Integration: Duplicate extraction skipped in pipeline (via ExtractionPipelineService)
- [x] Integration: Unique extraction stored in both PostgreSQL and Qdrant
