"""Orchestrates multi-pass schema extraction across field groups."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from services.extraction.field_groups import FieldGroup
from services.extraction.chunk_merge import (
    field_cardinality,
    merge_chunk_results as merge_chunk_results_v2,
)
from services.extraction.extraction_items import (
    ChunkExtractionResult,
    EntityItem,
    FieldItem,
    ListValueItem,
    locate_in_source,
)
from services.extraction.grounding import (
    _coerce_quote,
    compute_chunk_grounding,
    compute_chunk_grounding_entities,
    ground_entity_item,
    ground_field_item,
    verify_quote_in_source,
)
from services.extraction.page_classifier import (
    ClassificationResult,
    PageClassifier,
)
from services.extraction.schema_extractor import SchemaExtractor
from services.extraction.schema_validator import SchemaValidator
from services.llm.chunking import chunk_document

if TYPE_CHECKING:
    from config import ClassificationConfig, ExtractionConfig
    from services.extraction.field_groups import FieldDefinition
    from services.extraction.schema_adapter import ExtractionContext
    from services.extraction.smart_classifier import SmartClassifier

logger = structlog.get_logger(__name__)

# Source-grounding threshold for quote-in-content verification
_SOURCE_GROUNDING_THRESHOLD = 0.8  # quote must be ≥80% word-match in source

# Reserved keys that are metadata, not entity list data
_ENTITY_RESERVED_KEYS = frozenset(
    {"confidence", "_quotes", "_truncated", "_conflicts", "_validation"}
)


def _collect_quotes(result: dict) -> list[str]:
    """Extract all quote strings from a result dict.

    Handles both structures:
    - Field groups: top-level ``_quotes`` dict mapping field names to quotes
    - Entity lists: per-entity ``_quote`` strings inside list values

    Returns list of non-empty coerced quote strings.
    """
    quotes: list[str] = []

    # Field group structure: {"_quotes": {"field_name": "quote text"}}
    top_quotes = result.get("_quotes", {}) or {}
    if isinstance(top_quotes, dict):
        for raw_quote in top_quotes.values():
            coerced = _coerce_quote(raw_quote)
            if coerced:
                quotes.append(coerced)

    # Entity list structure: {"products": [{"name": "X", "_quote": "..."}, ...]}
    # Only scan if no top-level quotes found (entity lists don't have _quotes)
    if not quotes:
        for key, value in result.items():
            if key in _ENTITY_RESERVED_KEYS or not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                raw_quote = item.get("_quote")
                coerced = _coerce_quote(raw_quote)
                if coerced:
                    quotes.append(coerced)

    return quotes


def _source_grounding_ratio(result: dict, content: str) -> float:
    """Compute fraction of quoted fields whose quotes exist in the source content.

    Handles both field group results (top-level ``_quotes`` dict) and entity list
    results (per-entity ``_quote`` strings).

    Returns ratio of source-grounded quotes (score >= threshold) to total quotes.
    Returns 1.0 if no quotes exist (nothing to verify).
    """
    quotes = _collect_quotes(result)
    if not quotes:
        return 1.0

    grounded = sum(
        1
        for quote in quotes
        if verify_quote_in_source(quote, content) >= _SOURCE_GROUNDING_THRESHOLD
    )
    return grounded / len(quotes)


class SchemaExtractionOrchestrator:
    """Orchestrates extraction across all field groups for a source."""

    def __init__(
        self,
        schema_extractor: SchemaExtractor,
        *,
        extraction_config: "ExtractionConfig | None" = None,
        classification_config: "ClassificationConfig | None" = None,
        context: "ExtractionContext | None" = None,
        smart_classifier: "SmartClassifier | None" = None,
    ):
        """Initialize the orchestrator.

        Args:
            schema_extractor: Extractor for field groups.
            extraction_config: Extraction settings facade.
            classification_config: Classification settings facade.
            context: Extraction context configuration.
            smart_classifier: Optional smart classifier for embedding-based
                classification. When provided and classification is enabled,
                uses semantic similarity for field group selection.
        """
        from services.extraction.schema_adapter import ExtractionContext

        self._extractor = schema_extractor
        if extraction_config is None:
            from config import settings

            logger.debug("schema_orchestrator_using_global_extraction_config")
            extraction_config = settings.extraction
        if classification_config is None:
            from config import settings

            logger.debug("schema_orchestrator_using_global_classification_config")
            classification_config = settings.classification
        self._extraction = extraction_config
        self._classification = classification_config
        self._context = context or ExtractionContext()
        self._smart_classifier = smart_classifier

    async def extract_all_groups(
        self,
        source_id: UUID,
        markdown: str,
        source_context: str,
        field_groups: list[FieldGroup],
        source_url: str | None = None,
        source_title: str | None = None,
    ) -> tuple[list[dict], ClassificationResult | None]:
        """Extract all field groups from source content.

        Args:
            source_id: Source UUID for tracking.
            markdown: Markdown content.
            source_context: Source context (e.g., company name, website name).
            field_groups: Field groups to extract (REQUIRED).
            source_url: Source URL for classification.
            source_title: Source title for classification.

        Returns:
            Tuple of (extraction results, classification result or None).
        """
        context_value = source_context
        classification: ClassificationResult | None = None

        if not field_groups:
            logger.error(
                "extract_all_groups_no_field_groups",
                source_id=str(source_id),
                message="field_groups parameter is required but was empty",
            )
            return [], None

        # Classify page if URL is available and classification is enabled
        if source_url and self._classification.enabled:
            # Use smart classifier if available and enabled
            if self._smart_classifier and self._classification.smart_enabled:
                classification = await self._smart_classifier.classify(
                    url=source_url,
                    title=source_title,
                    content=markdown,
                    field_groups=field_groups,
                )
                classification_method = "smart"
            else:
                # Fall back to rule-based classification
                available_group_names = [g.name for g in field_groups]
                classifier = PageClassifier(available_groups=available_group_names)
                classification = classifier.classify(url=source_url, title=source_title)
                classification_method = "rule"

            logger.info(
                "page_classified",
                source_id=str(source_id),
                url=source_url,
                page_type=classification.page_type,
                relevant_groups=classification.relevant_groups,
                skip=classification.skip_extraction,
                confidence=classification.confidence,
                method=classification_method,
            )

            # Only skip if both classification says skip AND skip is enabled
            if classification.skip_extraction and self._classification.skip_enabled:
                logger.info(
                    "skipping_extraction",
                    source_id=str(source_id),
                    reason=classification.reasoning,
                )
                return [], classification

            # Filter field groups if classification found specific matches
            if classification.relevant_groups:
                relevant_names = set(classification.relevant_groups)
                field_groups = [g for g in field_groups if g.name in relevant_names]

                if not field_groups:
                    logger.warning(
                        "no_matching_field_groups",
                        source_id=str(source_id),
                        classified_groups=classification.relevant_groups,
                    )
                    return [], classification

        groups = field_groups
        results = []
        from services.extraction.extraction_items import safe_data_version

        data_version = safe_data_version(self._extraction)

        # Chunk document for large content
        # Reduce max_tokens by overlap so chunk + prepended overlap fits
        # within EXTRACTION_CONTENT_LIMIT (both are ~4 chars/token aligned)
        overlap = self._extraction.chunk_overlap_tokens
        effective_max = self._extraction.chunk_max_tokens - overlap
        chunks = chunk_document(
            markdown,
            max_tokens=effective_max,
            overlap_tokens=overlap,
        )

        logger.info(
            "schema_extraction_started",
            source_id=str(source_id),
            source=context_value,
            groups=len(groups),
            chunks=len(chunks),
            data_version=data_version,
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
                "grounding_scores": {},
                "data_version": data_version,
            }

            if data_version >= 2:
                return await self._extract_group_v2(
                    chunks, group, context_value, markdown, group_result
                )

            # v1 path (unchanged)
            chunk_results = await self._extract_chunks_batched(
                chunks=chunks,
                group=group,
                source_context=context_value,
            )

            if chunk_results:
                merged = self._merge_chunk_results(chunk_results, group)

                if self._extraction.validation_enabled:
                    validator = SchemaValidator(
                        min_confidence=self._extraction.validation_min_confidence,
                    )
                    merged, _ = validator.validate(merged, group)

                group_result["grounding_scores"] = merged.pop(
                    "_grounding_scores", {}
                )
                group_result["data"] = merged

                raw_confidence = merged.get("confidence", 0.0)
                is_empty, _ = self._is_empty_result(merged, group)

                if is_empty:
                    group_result["confidence"] = min(raw_confidence, 0.1)
                else:
                    group_result["confidence"] = raw_confidence

            return group_result

        # Run all field groups in parallel
        results = await asyncio.gather(*[extract_group(g) for g in groups])

        logger.info(
            "schema_extraction_completed",
            source_id=str(source_id),
            results_count=len(results),
        )

        return results, classification

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
        max_concurrent = self._extraction.max_concurrent_chunks
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_chunk_with_semaphore(chunk, chunk_idx: int) -> dict | None:
            """Extract from a single chunk with semaphore-controlled concurrency.

            After extraction, verifies that LLM-provided quotes actually exist
            in the chunk content. If too many quotes are fabricated, re-extracts
            once with a stricter quoting prompt.
            """
            async with semaphore:
                try:
                    result = await self._extractor.extract_field_group(
                        content=chunk.content,
                        field_group=group,
                        source_context=source_context,
                    )
                except Exception as e:
                    logger.error(
                        "chunk_extraction_failed",
                        group=group.name,
                        chunk_idx=chunk_idx,
                        error=str(e),
                    )
                    return None

                # Compute per-field source grounding scores for this chunk
                # (quote vs source content, all field types)
                chunk_sg = compute_chunk_grounding(result, chunk.content)
                entity_sg = compute_chunk_grounding_entities(
                    result, chunk.content
                )
                result["_source_grounding"] = {**chunk_sg, **entity_sg}

                # Source-grounding: verify quotes exist in chunk content
                if not self._extraction.source_quoting_enabled:
                    return result

                sg_ratio = _source_grounding_ratio(result, chunk.content)
                if sg_ratio >= self._extraction.source_grounding_min_ratio:
                    return result

                # Too many fabricated quotes — retry with stricter prompt
                logger.info(
                    "source_grounding_retry",
                    group=group.name,
                    chunk_idx=chunk_idx,
                    source_grounding_ratio=sg_ratio,
                )
                try:
                    retry_result = await self._extractor.extract_field_group(
                        content=chunk.content,
                        field_group=group,
                        source_context=source_context,
                        strict_quoting=True,
                    )
                except Exception:
                    return result  # keep original on retry failure

                # Compute source grounding for retry result
                retry_sg = compute_chunk_grounding(retry_result, chunk.content)
                retry_entity_sg = compute_chunk_grounding_entities(
                    retry_result, chunk.content
                )
                retry_result["_source_grounding"] = {
                    **retry_sg,
                    **retry_entity_sg,
                }

                retry_ratio = _source_grounding_ratio(retry_result, chunk.content)
                if retry_ratio > sg_ratio:
                    logger.info(
                        "source_grounding_retry_improved",
                        group=group.name,
                        chunk_idx=chunk_idx,
                        before=sg_ratio,
                        after=retry_ratio,
                    )
                    return retry_result
                return result

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

    def _pick_highest_confidence(
        self, field_name: str, chunk_results: list[dict]
    ) -> Any:
        """Pick value from the chunk with highest confidence.

        Args:
            field_name: Name of the field to pick.
            chunk_results: Results from each chunk.

        Returns:
            Value from highest-confidence chunk, or None.
        """
        best_val = None
        best_conf = -1.0
        for r in chunk_results:
            v = r.get(field_name)
            if v is not None:
                c = r.get("confidence") or 0.5
                if c > best_conf:
                    best_conf = c
                    best_val = v
        return best_val

    def _get_merge_strategy(self, field: "FieldDefinition") -> str:
        """Resolve merge strategy for a field.

        Priority: explicit field.merge_strategy > type-based default.

        Args:
            field: Field definition.

        Returns:
            Merge strategy string.
        """
        if field.merge_strategy:
            return field.merge_strategy

        # Type-based defaults
        if field.field_type == "boolean":
            return "majority_vote"
        elif field.field_type in ("integer", "float"):
            return "highest_confidence"
        elif field.field_type == "list":
            return "merge_dedupe"
        elif field.field_type == "enum":
            return "highest_confidence"
        else:  # text
            return "highest_confidence"

    def _merge_chunk_results(
        self, chunk_results: list[dict], group: FieldGroup
    ) -> dict:
        """Merge results from multiple chunks.

        Strategy resolution per field (explicit override > type default):
        - boolean: majority_vote
        - integer/float: highest_confidence
        - enum: highest_confidence
        - text: highest_confidence
        - list: merge_dedupe (flatten + deduplicate)
        - entity_list: merge all, dedupe by ID fields
        """
        if not chunk_results:
            return {}

        if group.is_entity_list:
            return self._merge_entity_lists(chunk_results, group)

        merged = {}
        for field in group.fields:
            values = [
                r.get(field.name)
                for r in chunk_results
                if r.get(field.name) is not None
            ]

            if not values:
                continue

            strategy = self._get_merge_strategy(field)

            if strategy == "majority_vote":
                # Any credible True wins at chunk level. LLMs return explicit
                # False when a chunk lacks evidence (not when evidence
                # contradicts), so majority vote biases toward False.
                # See TODO_downstream_trials.md Trial 2A: any_true=86% vs
                # majority_vote=48%.
                if any(v is True for v in values):
                    merged[field.name] = True
                elif any(v is False for v in values):
                    merged[field.name] = False
            elif strategy == "max":
                merged[field.name] = max(values)
            elif strategy == "min":
                merged[field.name] = min(values)
            elif strategy == "concat":
                unique_texts = list(
                    dict.fromkeys(str(v) for v in values if v is not None)
                )
                if len(unique_texts) > 1:
                    merged[field.name] = "; ".join(unique_texts)
                elif unique_texts:
                    merged[field.name] = unique_texts[0]
            elif strategy == "merge_dedupe":
                flat = []
                for v in values:
                    if isinstance(v, list):
                        flat.extend(v)
                    else:
                        flat.append(v)
                # Dedupe - handle both hashable (strings) and unhashable (dicts)
                if flat and isinstance(flat[0], dict):
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
            else:  # highest_confidence (default for numeric, text, enum)
                val = self._pick_highest_confidence(field.name, chunk_results)
                if val is not None:
                    merged[field.name] = val

        # Average confidence from chunks that actually returned it.
        # Skip chunks where LLM omitted confidence to avoid diluting the
        # average (e.g., 0.9 + missing → 0.9, not (0.9+0.5)/2 = 0.7).
        confidences = [
            r["confidence"]
            for r in chunk_results
            if r.get("confidence") is not None
        ]
        merged["confidence"] = (
            sum(confidences) / len(confidences) if confidences else 0.5
        )

        # Merge source quotes: keep quote from highest-confidence chunk per field
        if self._extraction.source_quoting_enabled:
            merged_quotes: dict[str, str] = {}
            best_conf: dict[str, float] = {}
            for result in chunk_results:
                chunk_quotes = result.get("_quotes", {})
                chunk_conf = result.get("confidence", 0.5)
                if isinstance(chunk_quotes, dict):
                    for field_name, quote in chunk_quotes.items():
                        if (
                            field_name not in best_conf
                            or chunk_conf > best_conf[field_name]
                        ):
                            merged_quotes[field_name] = quote
                            best_conf[field_name] = chunk_conf
            if merged_quotes:
                merged["_quotes"] = merged_quotes

        # Detect merge conflicts between chunks
        if self._extraction.conflict_detection_enabled and len(chunk_results) > 1:
            conflicts = self._detect_conflicts(chunk_results, group, merged)
            if conflicts:
                merged["_conflicts"] = conflicts

        # Propagate source grounding scores from chunks.
        # For each field, use the score from the chunk whose quote was selected
        # (highest confidence). All field types are scored — quote vs source.
        grounding_scores: dict[str, float] = {}
        merged_quotes = merged.get("_quotes", {}) or {}
        for field_name, quote in merged_quotes.items():
            # Find the chunk this quote came from
            for result in chunk_results:
                chunk_quotes = result.get("_quotes", {}) or {}
                if chunk_quotes.get(field_name) == quote:
                    sg = result.get("_source_grounding", {})
                    grounding_scores[field_name] = sg.get(field_name, 0.0)
                    break
            else:
                # Quote not matched to any chunk — score 0.0
                grounding_scores[field_name] = 0.0
        merged["_grounding_scores"] = grounding_scores

        return merged

    def _merge_entity_lists(
        self, chunk_results: list[dict], group: FieldGroup | None = None
    ) -> dict:
        """Merge entity lists from multiple chunks.

        Uses group.name as the expected entity key, falling back to scanning
        chunk results for the first list value.
        """
        reserved_keys = {"confidence", "error", "status"}
        entity_key = None

        # Prefer group.name as the entity key
        if group is not None:
            entity_key = group.name

        # Fall back to scanning chunk results
        if not entity_key:
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
                    raw_id = entity.get(id_field)
                    if raw_id is not None and raw_id != "":
                        entity_id = str(raw_id).strip().lower()
                        break

                if entity_id and entity_id not in seen_ids:
                    seen_ids.add(entity_id)
                    all_entities.append(entity)
                elif not entity_id:
                    # No ID field - dedupe by content hash
                    entity_hash = hashlib.sha256(
                        json.dumps(entity, sort_keys=True).encode()
                    ).hexdigest()[:16]
                    if entity_hash not in seen_ids:
                        seen_ids.add(entity_hash)
                        all_entities.append(entity)

        # Average confidence from chunks that actually returned it
        confidences = [
            r["confidence"]
            for r in chunk_results
            if r.get("confidence") is not None
        ]
        avg_confidence = (
            sum(confidences) / len(confidences) if confidences else 0.5
        )

        merged = {
            entity_key: all_entities,
            "confidence": avg_confidence,
        }

        # Propagate truncation flag if any chunk was truncated
        if any(r.get("_truncated") for r in chunk_results):
            merged["_truncated"] = True

        # Propagate entity-level source grounding scores from chunks.
        # Each chunk has _source_grounding with entity key -> average score.
        # Combine scores from all chunks that contributed entities.
        if all_entities and group is not None:
            chunk_entity_scores: list[float] = []
            for result in chunk_results:
                sg = result.get("_source_grounding", {})
                if entity_key in sg:
                    chunk_entity_scores.append(sg[entity_key])
            if chunk_entity_scores:
                merged["_grounding_scores"] = {
                    entity_key: round(
                        sum(chunk_entity_scores) / len(chunk_entity_scores), 4
                    )
                }
            else:
                merged["_grounding_scores"] = {}
        else:
            merged["_grounding_scores"] = {}

        return merged

    def _detect_conflicts(
        self,
        chunk_results: list[dict],
        group: FieldGroup,
        merged: dict,
    ) -> dict:
        """Detect disagreements between chunk results for non-entity fields.

        Args:
            chunk_results: Raw results from each chunk.
            group: Field group definition.
            merged: Already-merged result dict.

        Returns:
            Dict mapping field names to conflict info, empty if no conflicts.
        """
        conflicts: dict = {}

        for field in group.fields:
            values_with_chunk = [
                (i, r.get(field.name))
                for i, r in enumerate(chunk_results)
                if r.get(field.name) is not None
            ]

            if len(values_with_chunk) < 2:
                continue

            values_only = [v for _, v in values_with_chunk]

            has_conflict = False
            strategy = self._get_merge_strategy(field)

            if field.field_type == "boolean":
                unique = set(values_only)
                if len(unique) > 1:
                    has_conflict = True
            elif field.field_type in ("integer", "float"):
                try:
                    nums = [float(v) for v in values_only]
                    max_val = max(abs(n) for n in nums)
                    if max_val > 0:
                        spread = (max(nums) - min(nums)) / max_val
                        if spread > 0.1:
                            has_conflict = True
                except (TypeError, ValueError):
                    pass
            else:  # text, enum
                unique = set(str(v) for v in values_only)
                if len(unique) > 1:
                    has_conflict = True

            if has_conflict:
                conflicts[field.name] = {
                    "values": [
                        {"chunk": idx, "value": val} for idx, val in values_with_chunk
                    ],
                    "resolution": strategy,
                    "resolved_value": merged.get(field.name),
                }

        return conflicts

    async def _extract_group_v2(
        self,
        chunks: list,
        group: FieldGroup,
        source_context: str,
        full_content: str,
        group_result: dict,
    ) -> dict:
        """v2 extraction: per-field structured data with inline grounding.

        Extracts from chunks, parses to ChunkExtractionResult, grounds each
        item inline, then merges using cardinality-based strategies.
        """
        max_concurrent = self._extraction.max_concurrent_chunks
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_chunk_v2(chunk, chunk_idx: int) -> ChunkExtractionResult | None:
            async with semaphore:
                try:
                    raw = await self._extractor.extract_field_group(
                        content=chunk.content,
                        field_group=group,
                        source_context=source_context,
                    )
                except Exception as e:
                    logger.error(
                        "v2_chunk_extraction_failed",
                        group=group.name,
                        chunk_idx=chunk_idx,
                        error=str(e),
                    )
                    return None

                return self._parse_chunk_to_v2(raw, group, chunk, chunk_idx, full_content)

        tasks = [extract_chunk_v2(chunk, idx) for idx, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        chunk_results = [r for r in results if r is not None]

        if not chunk_results:
            return group_result

        # Merge using cardinality-based strategies
        merged = merge_chunk_results_v2(
            chunk_results, group, self._context.entity_id_fields
        )

        # Validate v2 format
        if self._extraction.validation_enabled:
            validator = SchemaValidator(
                min_confidence=self._extraction.validation_min_confidence,
            )
            merged, _ = validator.validate(merged, group)

        group_result["data"] = merged
        group_result["grounding_scores"] = None  # Grounding is inside data for v2

        # Compute aggregate confidence from field items
        confidences = self._extract_v2_confidences(merged)
        if confidences:
            group_result["confidence"] = sum(confidences) / len(confidences)

        return group_result

    def _parse_chunk_to_v2(
        self,
        raw: dict,
        group: FieldGroup,
        chunk: Any,
        chunk_idx: int,
        full_content: str,
    ) -> ChunkExtractionResult:
        """Parse raw LLM response into ChunkExtractionResult with inline grounding."""
        if group.is_entity_list:
            return self._parse_entity_chunk_v2(raw, group, chunk, chunk_idx, full_content)

        # Parse via SchemaExtractor's v2 parser (handles fallback from v1)
        parsed = SchemaExtractor.parse_v2_response(raw, group)
        fields_data = parsed.get("fields", {})

        field_items: dict[str, FieldItem] = {}
        list_items: dict[str, list[ListValueItem]] = {}

        for field_def in group.fields:
            entry = fields_data.get(field_def.name, {})
            value = entry.get("value") if isinstance(entry, dict) else None
            confidence = float(entry.get("confidence", 0.5)) if isinstance(entry, dict) else 0.5
            quote = entry.get("quote") if isinstance(entry, dict) else None

            card = field_cardinality(field_def)

            if card == "multi_value" and isinstance(value, list):
                items = []
                for v in value:
                    grounding = ground_field_item(
                        field_def.name, v, quote, chunk.content, field_def.field_type
                    )
                    location = locate_in_source(quote, full_content, chunk)
                    items.append(ListValueItem(v, quote, grounding, location))
                list_items[field_def.name] = items
            else:
                # Single, boolean, summary
                grounding = ground_field_item(
                    field_def.name, value, quote, chunk.content, field_def.field_type
                )
                location = locate_in_source(quote, full_content, chunk)
                field_items[field_def.name] = FieldItem(
                    value=value,
                    confidence=confidence,
                    quote=quote,
                    grounding=grounding,
                    location=location,
                )

        return ChunkExtractionResult(
            chunk_index=chunk_idx,
            field_items=field_items,
            list_items=list_items,
        )

    def _parse_entity_chunk_v2(
        self,
        raw: dict,
        group: FieldGroup,
        chunk: Any,
        chunk_idx: int,
        full_content: str,
    ) -> ChunkExtractionResult:
        """Parse entity list LLM response into ChunkExtractionResult."""
        parsed = SchemaExtractor.parse_v2_entity_response(raw, group)
        entity_list = parsed.get(group.name, [])

        entities: list[EntityItem] = []
        for entity_data in entity_list:
            fields = entity_data.get("fields", {})
            confidence = float(entity_data.get("_confidence", 0.5))
            quote = entity_data.get("_quote")

            grounding = ground_entity_item(quote, chunk.content)
            location = locate_in_source(quote, full_content, chunk)

            entities.append(EntityItem(
                fields=fields,
                confidence=confidence,
                quote=quote,
                grounding=grounding,
                location=location,
            ))

        return ChunkExtractionResult(
            chunk_index=chunk_idx,
            entity_items={group.name: entities},
        )

    async def _extract_entities_paginated(
        self,
        chunk_content: str,
        field_group: FieldGroup,
        source_context: str | None,
    ) -> tuple[list[dict], bool]:
        """Iterative entity extraction with stall/convergence detection.

        Safety controls:
        1. has_more=False from LLM → stop
        2. Empty response (0 new entities) → stop
        3. Consecutive stall (same entities returned 2x) → stop
        4. Total entities >= max_items → stop
        5. Max iterations safety cap

        Returns:
            Tuple of (all_entities, has_more_flag).
        """
        max_items = field_group.max_items or 50
        batch_size = min(max_items, 20)
        max_iterations = (max_items // batch_size) + 3  # safety margin
        MAX_CONSECUTIVE_STALLS = 2

        all_entities: list[dict] = []
        already_found_ids: list[str] = []
        consecutive_stalls = 0

        id_fields = self._context.entity_id_fields

        for iteration in range(max_iterations):
            try:
                raw = await self._extractor.extract_field_group(
                    content=chunk_content,
                    field_group=field_group,
                    source_context=source_context,
                )
            except Exception as e:
                logger.error(
                    "entity_pagination_extraction_failed",
                    group=field_group.name,
                    iteration=iteration,
                    error=str(e),
                )
                break

            parsed = SchemaExtractor.parse_v2_entity_response(raw, field_group)
            new_raw_entities = parsed.get(field_group.name, [])
            has_more = parsed.get("has_more", False)

            # Filter to truly new entities
            new_entities = []
            for ent in new_raw_entities:
                ent_id = self._extract_entity_id(ent.get("fields", {}), id_fields)
                if ent_id and ent_id in {x.lower() for x in already_found_ids}:
                    continue
                new_entities.append(ent)

            if not new_entities:
                break

            # Stall detection: all "new" entities are actually duplicates
            new_ids = [
                self._extract_entity_id(e.get("fields", {}), id_fields)
                for e in new_entities
            ]
            existing_ids = {x.lower() for x in already_found_ids}
            if all(nid and nid in existing_ids for nid in new_ids):
                consecutive_stalls += 1
                if consecutive_stalls >= MAX_CONSECUTIVE_STALLS:
                    logger.info(
                        "entity_pagination_stalled",
                        group=field_group.name,
                        total=len(all_entities),
                    )
                    break
            else:
                consecutive_stalls = 0

            all_entities.extend(new_entities)

            # Track found IDs for next iteration
            for ent in new_entities:
                eid = self._extract_entity_id(ent.get("fields", {}), id_fields)
                if eid:
                    already_found_ids.append(eid)

            if not has_more:
                break
            if len(all_entities) >= max_items:
                break

        return all_entities, len(all_entities) >= max_items

    @staticmethod
    def _extract_entity_id(fields: dict, id_fields: list[str]) -> str | None:
        """Extract entity ID string for dedup tracking."""
        for field in id_fields:
            val = fields.get(field)
            if val is not None and str(val).strip():
                return str(val).strip().lower()
        return None

    @staticmethod
    def _extract_v2_confidences(data: dict) -> list[float]:
        """Extract confidence values from v2 structured data."""
        confidences: list[float] = []
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if not isinstance(value, dict):
                continue
            if "confidence" in value:
                confidences.append(float(value["confidence"]))
            elif "items" in value:
                for item in value["items"]:
                    if isinstance(item, dict) and "confidence" in item:
                        confidences.append(float(item["confidence"]))
        return confidences

    def _is_empty_result(self, data: dict, group: FieldGroup) -> tuple[bool, float]:
        """Check if extraction result is empty or default-only.

        Args:
            data: Extraction data (confidence already popped).
            group: Field group definition.

        Returns:
            Tuple of (is_empty, populated_ratio).
            is_empty: True if <20% of fields have real values.
            populated_ratio: Fraction of fields with non-default values (0.0-1.0).
        """
        if group.is_entity_list:
            # Skip metadata keys — only check actual entity list data
            skip_keys = {"confidence", "_quotes", "_conflicts", "_validation"}
            for key, value in data.items():
                if key in skip_keys:
                    continue
                if isinstance(value, list) and len(value) > 0:
                    return False, 1.0
            return True, 0.0

        total = 0
        populated = 0
        for field_def in group.fields:
            if field_def.name == "confidence":
                continue
            total += 1
            value = data.get(field_def.name)
            if value is None:
                continue
            if field_def.default is not None and value == field_def.default:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list) and len(value) == 0:
                continue
            populated += 1

        if total == 0:
            return True, 0.0
        ratio = populated / total
        return ratio < 0.2, ratio
