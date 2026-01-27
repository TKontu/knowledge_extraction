"""Orchestrates multi-pass schema extraction across field groups."""

import asyncio
from uuid import UUID

import structlog

from config import settings
from services.extraction.field_groups import FieldGroup
from services.extraction.schema_extractor import SchemaExtractor
from services.llm.chunking import chunk_document

logger = structlog.get_logger(__name__)


class SchemaExtractionOrchestrator:
    """Orchestrates extraction across all field groups for a source."""

    def __init__(
        self,
        schema_extractor: SchemaExtractor,
        context: "ExtractionContext | None" = None,
    ):
        from services.extraction.schema_adapter import ExtractionContext

        self._extractor = schema_extractor
        self._context = context or ExtractionContext()

    async def extract_all_groups(
        self,
        source_id: UUID,
        markdown: str,
        source_context: str,
        field_groups: list[FieldGroup],
        company_name: str | None = None,  # Deprecated, backward compat
    ) -> list[dict]:
        """Extract all field groups from source content.

        Args:
            source_id: Source UUID for tracking.
            markdown: Markdown content.
            source_context: Source context (e.g., company name, website name).
            field_groups: Field groups to extract (REQUIRED).
            company_name: DEPRECATED. Use source_context instead.

        Returns:
            List of extraction results, one per field group.
        """
        # Backward compatibility
        context_value = source_context if source_context is not None else company_name

        if not field_groups:
            logger.error(
                "extract_all_groups_no_field_groups",
                source_id=str(source_id),
                message="field_groups parameter is required but was empty",
            )
            return []

        groups = field_groups
        results = []

        # Chunk document for large content
        chunks = chunk_document(markdown)

        logger.info(
            "schema_extraction_started",
            source_id=str(source_id),
            source=context_value,
            groups=len(groups),
            chunks=len(chunks),
        )

        # Extract all field groups in parallel for better KV cache utilization
        async def extract_group(group: FieldGroup) -> dict:
            """Extract a single field group from all chunks with batching."""
            group_result = {
                "extraction_type": group.name,
                "source_id": source_id,
                "source_group": context_value,
                "data": {},
                "confidence": 0.0,
            }

            # Extract from chunks in parallel batches
            chunk_results = await self._extract_chunks_batched(
                chunks=chunks,
                group=group,
                source_context=context_value,
            )

            # Merge chunk results
            if chunk_results:
                merged = self._merge_chunk_results(chunk_results, group)
                group_result["data"] = merged
                group_result["confidence"] = merged.pop("confidence", 0.8)

            return group_result

        # Run all field groups in parallel
        results = await asyncio.gather(*[extract_group(g) for g in groups])

        logger.info(
            "schema_extraction_completed",
            source_id=str(source_id),
            results_count=len(results),
        )

        return results

    async def _extract_chunks_batched(
        self,
        chunks: list,
        group: FieldGroup,
        source_context: str,
    ) -> list[dict]:
        """Extract from chunks with continuous concurrency control.

        Uses a semaphore for continuous request flow instead of batch-and-wait.
        This keeps the vLLM KV cache consistently utilized by allowing new
        requests to start immediately as old ones complete.

        Args:
            chunks: List of document chunks to process.
            group: Field group to extract.
            source_context: Source context for extraction.

        Returns:
            List of extraction results from successful chunks.
        """
        max_concurrent = settings.extraction_max_concurrent_chunks
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_chunk_with_semaphore(
            chunk, chunk_idx: int, max_retries: int = 3
        ) -> dict | None:
            """Extract from a single chunk with semaphore-controlled concurrency."""
            async with semaphore:
                for attempt in range(max_retries):
                    try:
                        result = await self._extractor.extract_field_group(
                            content=chunk.content,
                            field_group=group,
                            source_context=source_context,
                        )
                        return result
                    except Exception as e:
                        if attempt < max_retries - 1:
                            backoff = min(
                                settings.llm_retry_backoff_max,
                                settings.llm_retry_backoff_min * (2**attempt),
                            )
                            logger.warning(
                                "chunk_extraction_retry",
                                group=group.name,
                                chunk_idx=chunk_idx,
                                attempt=attempt + 1,
                                backoff=backoff,
                                error=str(e),
                            )
                            await asyncio.sleep(backoff)
                        else:
                            logger.error(
                                "chunk_extraction_failed",
                                group=group.name,
                                chunk_idx=chunk_idx,
                                error=str(e),
                            )
                            return None

        logger.debug(
            "processing_chunks_continuous",
            group=group.name,
            total_chunks=len(chunks),
            max_concurrent=max_concurrent,
        )

        # Launch all chunks immediately - semaphore controls concurrency
        # This enables continuous flow: new requests start as old ones finish
        tasks = [
            extract_chunk_with_semaphore(chunk, idx) for idx, chunk in enumerate(chunks)
        ]
        results = await asyncio.gather(*tasks)

        # Filter out None results from failed extractions
        chunk_results = [r for r in results if r is not None]

        logger.info(
            "chunk_extraction_completed",
            group=group.name,
            successful=len(chunk_results),
            total=len(chunks),
        )

        return chunk_results

    def _merge_chunk_results(
        self, chunk_results: list[dict], group: FieldGroup
    ) -> dict:
        """Merge results from multiple chunks.

        Aggregation rules:
        - boolean: True if ANY chunk says True
        - integer/float: Take maximum
        - text/enum: Dedupe and concatenate unique values with "; "
        - list: Merge and dedupe
        - entity_list: Merge all, dedupe by ID fields
        """
        if not chunk_results:
            return {}

        if group.is_entity_list:
            return self._merge_entity_lists(chunk_results)

        merged = {}
        for field in group.fields:
            values = [
                r.get(field.name)
                for r in chunk_results
                if r.get(field.name) is not None
            ]

            if not values:
                continue

            if field.field_type == "boolean":
                merged[field.name] = any(values)
            elif field.field_type in ("integer", "float"):
                merged[field.name] = max(values)
            elif field.field_type == "list":
                flat = []
                for v in values:
                    if isinstance(v, list):
                        flat.extend(v)
                    else:
                        flat.append(v)
                # Dedupe - handle both hashable (strings) and unhashable (dicts)
                if flat and isinstance(flat[0], dict):
                    # For list of dicts, dedupe by JSON string representation
                    import json

                    seen = set()
                    unique = []
                    for item in flat:
                        key = json.dumps(item, sort_keys=True)
                        if key not in seen:
                            seen.add(key)
                            unique.append(item)
                    merged[field.name] = unique
                else:
                    merged[field.name] = list(dict.fromkeys(flat))
            else:  # text, enum
                # Dedupe and concatenate unique values to preserve information
                unique_texts = list(
                    dict.fromkeys(str(v) for v in values if v is not None)
                )
                if len(unique_texts) > 1:
                    merged[field.name] = "; ".join(unique_texts)
                elif unique_texts:
                    merged[field.name] = unique_texts[0]

        # Average confidence
        confidences = [r.get("confidence", 0.8) for r in chunk_results]
        merged["confidence"] = sum(confidences) / len(confidences)

        return merged

    def _merge_entity_lists(self, chunk_results: list[dict]) -> dict:
        """Merge entity lists from multiple chunks.

        Dynamically detects the entity list key (e.g., "products", "employees",
        "locations") and deduplicates by common ID fields.
        """
        # Find which key contains the entity list by looking for any list value
        # (excluding 'confidence' and other known non-list keys)
        reserved_keys = {"confidence", "error", "status"}
        entity_key = None

        for result in chunk_results:
            for key, value in result.items():
                if key not in reserved_keys and isinstance(value, list):
                    entity_key = key
                    break
            if entity_key:
                break

        # Default to "entities" if no entity list found
        if not entity_key:
            entity_key = "entities"

        all_entities = []
        seen_ids = set()

        for result in chunk_results:
            entities = result.get(entity_key, [])
            if not isinstance(entities, list):
                continue

            for entity in entities:
                if not isinstance(entity, dict):
                    continue

                # Use context-defined ID fields for deduplication
                entity_id = None
                for id_field in self._context.entity_id_fields:
                    if entity.get(id_field):
                        entity_id = entity.get(id_field)
                        break

                if entity_id and entity_id not in seen_ids:
                    seen_ids.add(entity_id)
                    all_entities.append(entity)
                elif not entity_id:
                    # No ID field - include but can't dedupe
                    all_entities.append(entity)

        confidences = [r.get("confidence", 0.8) for r in chunk_results]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.8

        return {
            entity_key: all_entities,
            "confidence": avg_confidence,
        }
