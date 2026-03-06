"""Orchestrates multi-pass schema extraction across field groups."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from services.extraction.field_groups import FieldGroup
from services.extraction.grounding import (
    GROUNDING_DEFAULTS,
    _METADATA_KEYS as _GROUNDING_METADATA_KEYS,
    _coerce_quote,
    score_field,
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

                # Schema-aware validation (before confidence pop)
                if self._extraction.validation_enabled:
                    validator = SchemaValidator(
                        min_confidence=self._extraction.validation_min_confidence,
                    )
                    merged, _ = validator.validate(merged, group)

                # Extract pre-computed per-chunk grounding scores
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

        # Compute per-chunk grounding scores with aligned value+quote pairs.
        # For each field, score each chunk's OWN value against its OWN quote,
        # then keep the best score. This avoids misalignment when the merged
        # value comes from a different chunk than the merged quote (e.g.,
        # merge_dedupe, majority_vote strategies).
        grounding_scores: dict[str, float] = {}
        for field in group.fields:
            if field.name in _GROUNDING_METADATA_KEYS:
                continue
            ft = field.field_type
            grounding_mode = field.grounding_mode or GROUNDING_DEFAULTS.get(ft, "required")
            if grounding_mode in ("none", "semantic"):
                continue
            # Only score fields present in the merged result
            if field.name not in merged or merged[field.name] is None:
                continue
            best = 0.0
            for r in chunk_results:
                val = r.get(field.name)
                if val is None:
                    continue
                chunk_quote = (r.get("_quotes") or {}).get(field.name, "")
                if chunk_quote:
                    best = max(best, score_field(val, chunk_quote, ft))

            # Fallback: if no chunk scored > 0 (e.g. winning chunk had value
            # but no quote while a different chunk contributed the quote),
            # try scoring the merged value against the best available quote.
            if best == 0.0 and field.name in merged and merged[field.name] is not None:
                merged_quote = (merged.get("_quotes") or {}).get(field.name, "")
                if merged_quote:
                    best = score_field(merged[field.name], merged_quote, ft)

            grounding_scores[field.name] = best
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

        # Compute entity-level grounding scores.
        # For entity lists, each entity has its own _quote. We score each
        # entity's ID field value against its _quote, then report the average
        # as the grounding score for the entity list field.
        if all_entities and group is not None:
            id_field_names = self._context.entity_id_fields
            entity_scores: list[float] = []
            for entity in all_entities:
                if not isinstance(entity, dict):
                    continue
                raw_quote = entity.get("_quote")
                quote = _coerce_quote(raw_quote)
                if not quote:
                    entity_scores.append(0.0)
                    continue
                # Find the entity's identifying value to score against quote
                id_value = None
                for id_field in id_field_names:
                    id_value = entity.get(id_field)
                    if id_value is not None:
                        break
                if id_value is not None:
                    entity_scores.append(
                        score_field(id_value, quote, "string")
                    )
                else:
                    # No ID field — quote exists, assume grounded
                    entity_scores.append(1.0)

            if entity_scores:
                merged["_grounding_scores"] = {
                    entity_key: sum(entity_scores) / len(entity_scores)
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
