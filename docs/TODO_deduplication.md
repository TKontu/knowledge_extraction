# TODO: Deduplication Strategy

## Overview

Prevent duplicate facts using embedding similarity before storage.

## Status

**Current State:**
- ✅ Qdrant client available (`pipeline/qdrant_connection.py`)
- ✅ Embedding endpoint configured (BGE-large-en at `192.168.0.136:9003`)
- ⚠️ No deduplication logic implemented
- ⚠️ No embedding service

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
        self.collection = "techfacts"

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
    exists = any(c.name == "techfacts" for c in collections)

    if not exists:
        client.create_collection(
            collection_name="techfacts",
            vectors_config=VectorParams(
                size=1024,  # BGE-large-en dimension
                distance=Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection 'techfacts'")
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
  qdrant_collection: techfacts
  embedding_dimension: 1024    # BGE-large-en
```

---

## File Structure

```
pipeline/
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

- [ ] Create `EmbeddingService` class
- [ ] Create `FactDeduplicator` class
- [ ] Implement Qdrant collection initialization
- [ ] Integrate deduplication into extraction pipeline
- [ ] Add configuration loading
- [ ] Test with sample facts
- [ ] Tune threshold with real data (may need adjustment from 0.90)

---

## Testing Checklist

- [ ] Unit: Embedding service returns correct dimension (1024)
- [ ] Unit: Deduplicator detects exact duplicates
- [ ] Unit: Deduplicator detects near-duplicates above threshold
- [ ] Unit: Different facts below threshold are NOT flagged
- [ ] Integration: Qdrant collection created on startup
- [ ] Integration: Duplicate fact skipped in full pipeline
- [ ] Integration: Unique fact stored in both PostgreSQL and Qdrant
