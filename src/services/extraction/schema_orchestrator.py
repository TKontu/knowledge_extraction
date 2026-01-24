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

    def __init__(self, schema_extractor: SchemaExtractor):
        self._extractor = schema_extractor

    async def extract_all_groups(
        self,
        source_id: UUID,
        markdown: str,
        company_name: str,
        field_groups: list[FieldGroup],
    ) -> list[dict]:
        """Extract all field groups from source content.

        Args:
            source_id: Source UUID for tracking.
            markdown: Markdown content.
            company_name: Company name for context.
            field_groups: Field groups to extract (REQUIRED).

        Returns:
            List of extraction results, one per field group.
        """
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
            company=company_name,
            groups=len(groups),
            chunks=len(chunks),
        )

        # Extract all field groups in parallel for better KV cache utilization
        async def extract_group(group: FieldGroup) -> dict:
            """Extract a single field group from all chunks with batching."""
            group_result = {
                "extraction_type": group.name,
                "source_id": source_id,
                "source_group": company_name,
                "data": {},
                "confidence": 0.0,
            }

            # Extract from chunks in parallel batches
            chunk_results = await self._extract_chunks_batched(
                chunks=chunks,
                group=group,
                company_name=company_name,
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
        company_name: str,
    ) -> list[dict]:
        """Extract from chunks with continuous concurrency control.

        Uses a semaphore for continuous request flow instead of batch-and-wait.
        This keeps the vLLM KV cache consistently utilized by allowing new
        requests to start immediately as old ones complete.

        Args:
            chunks: List of document chunks to process.
            group: Field group to extract.
            company_name: Company name for context.

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
                            company_name=company_name,
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
        - integer: Take maximum
        - text: Take longest non-empty
        - list: Merge and dedupe
        - products: Merge all, dedupe by product_name
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
                merged[field.name] = max(values, key=lambda x: len(str(x)) if x else 0)

        # Average confidence
        confidences = [r.get("confidence", 0.8) for r in chunk_results]
        merged["confidence"] = sum(confidences) / len(confidences)

        return merged

    def _merge_entity_lists(self, chunk_results: list[dict]) -> dict:
        """Merge entity lists (products) from multiple chunks."""
        all_products = []
        seen_names = set()

        for result in chunk_results:
            products = result.get("products", [])
            for product in products:
                name = product.get("product_name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_products.append(product)

        confidences = [r.get("confidence", 0.8) for r in chunk_results]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.8

        return {
            "products": all_products,
            "confidence": avg_confidence,
        }
