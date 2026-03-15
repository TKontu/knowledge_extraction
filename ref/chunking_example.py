import logging
import re
from typing import Any

import tiktoken

from ..core.config import Settings

logger = logging.getLogger(__name__)

# Constants for token estimation and safety
CHARS_PER_TOKEN_ESTIMATE = 4  # Rough estimate for character-to-token ratio
TOKEN_WARNING_THRESHOLD = 0.9  # Warn when approaching 90% of token limit


class TextChunker:
    def __init__(self, config: Settings = None):
        from ..core.config import get_config

        if config is None:
            config = get_config()

        # Use unified chunking configuration
        chunking_config = config.rag.chunking
        self.target_sentences = chunking_config.target_sentences
        self.overlap_sentences = chunking_config.overlap_sentences
        self.max_chars = chunking_config.chunk_size

        # Two-tier chunking feature flag
        self.enable_two_tier = getattr(config.rag, "enable_two_tier_chunking", False)

        # Token-based limits for embedding safety (configurable)
        self.vector_max_tokens = getattr(
            config.rag, "embedding_max_tokens", 400
        )  # Safe limit for 512 token embedding models
        self.graphrag_max_tokens = (
            config.graphrag.embedding_batch_max_tokens
        )  # Use configured GraphRAG token limit

        # Initialize primary tokenizer (OpenAI)
        try:
            self.openai_tokenizer = tiktoken.get_encoding(
                "cl100k_base"
            )  # Standard OpenAI tokenizer
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to load cl100k_base tokenizer: {e}, using fallback")
            self.openai_tokenizer = tiktoken.encoding_for_model(
                "text-embedding-ada-002"
            )  # Fallback

        # Initialize BGE tokenizer for accurate embedding compatibility
        self.bge_tokenizer = None
        try:
            from transformers import AutoTokenizer

            self.bge_tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-large-en")
            logger.debug("BGE tokenizer loaded for embedding compatibility")
        except Exception as e:
            logger.warning(
                f"Could not load BGE tokenizer: {e}. Using OpenAI tokenizer only."
            )

        # Maintain backward compatibility
        self.tokenizer = self.openai_tokenizer

        # Simple sentence boundary pattern - matches periods, exclamation marks, and question marks
        self.sentence_pattern = re.compile(r"[.!?]+\s+")

        # Pattern for protecting abbreviations during sentence splitting
        self.abbreviation_pattern = re.compile(
            r"\b(Dr|Mr|Mrs|Ms|Prof|etc|vs|i\.e|e\.g)\."
        )

        tokenizer_info = "BGE+OpenAI" if self.bge_tokenizer else "OpenAI"
        chunking_mode = "Two-tier" if self.enable_two_tier else "Traditional"
        logger.debug(
            f"TextChunker initialized - Mode: {chunking_mode}, Unified chunking: {self.max_chars} chars max, "
            f"Vector: {self.vector_max_tokens} tokens max, GraphRAG: {self.graphrag_max_tokens} tokens max, "
            f"Target sentences: {self.target_sentences}, Overlap: {self.overlap_sentences}, "
            f"Tokenizers: {tokenizer_info}"
        )

    def _split_into_sentences(self, text: str) -> list[str]:
        """Split text into sentences while preserving punctuation."""
        # Protect common abbreviations that shouldn't break sentences
        text = self.abbreviation_pattern.sub(
            lambda m: m.group(0).replace(".", "<ABBREV>"), text
        )

        # Find sentence boundaries and split while preserving punctuation
        import re

        sentences = []
        current_pos = 0

        for match in re.finditer(self.sentence_pattern, text):
            # Get text from current position to end of sentence (including punctuation)
            sentence_end = match.start() + len(match.group().rstrip())
            sentence = text[current_pos:sentence_end].strip()

            if sentence:
                # Restore abbreviations and add to result
                sentence = sentence.replace("<ABBREV>", ".")
                sentences.append(sentence)

            current_pos = match.end()

        # Add any remaining text as final sentence
        if current_pos < len(text):
            final_sentence = text[current_pos:].strip()
            if final_sentence:
                final_sentence = final_sentence.replace("<ABBREV>", ".")
                sentences.append(final_sentence)

        return sentences

    def _split_into_sentence_objects(
        self, text: str, start_id: int = 0
    ) -> list[dict[str, Any]]:
        """Split text into sentence objects with unique IDs and token counts."""
        sentences = self._split_into_sentences(text)
        sentence_objects = []

        for i, sentence_text in enumerate(sentences):
            token_count = self._count_tokens(sentence_text)
            sentence_obj = {
                "id": start_id + i,
                "text": sentence_text,
                "token_count": token_count,
                "char_count": len(sentence_text),
            }
            sentence_objects.append(sentence_obj)

        return sentence_objects

    def process_document_sentences(
        self, text: str, page_mappings: list[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Process document into sentence objects with optional page mapping integration."""
        sentence_objects = self._split_into_sentence_objects(text)

        # Integrate page mappings if provided
        if page_mappings:
            for sentence_obj in sentence_objects:
                sentence_obj["page_info"] = self._find_sentence_pages(
                    sentence_obj, text, page_mappings
                )

        return {
            "sentences": sentence_objects,
            "total_sentences": len(sentence_objects),
            "total_tokens": sum(s["token_count"] for s in sentence_objects),
            "total_chars": sum(s["char_count"] for s in sentence_objects),
        }

    def _find_sentence_pages(
        self,
        sentence_obj: dict[str, Any],
        full_text: str,
        page_mappings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Find which pages a sentence spans across and collect associated metadata."""
        sentence_text = sentence_obj["text"]

        # Find sentence position in full text
        sentence_start = full_text.find(sentence_text)
        if sentence_start == -1:
            return {"pages": [], "page_range": None}

        sentence_end = sentence_start + len(sentence_text)

        # Find overlapping pages and collect metadata
        pages_involved = set()
        extraction_methods = []
        section_titles = []
        section_paths = []
        all_headers = []

        for page_info in page_mappings:
            page_start = page_info.get("start_pos", 0)
            page_end = page_info.get("end_pos", 0)
            page_number = page_info.get("page_number")

            # Check if sentence overlaps with this page
            if page_number and sentence_start < page_end and sentence_end > page_start:
                pages_involved.add(page_number)

                # Collect metadata from this page
                if page_info.get("extraction_method"):
                    extraction_methods.append(page_info["extraction_method"])

                if page_info.get("section_title"):
                    section_titles.append(page_info["section_title"])

                if page_info.get("section_path"):
                    section_paths.append(page_info["section_path"])

                if page_info.get("markdown_headers"):
                    all_headers.extend(page_info["markdown_headers"])

        pages_list = sorted(list(pages_involved))
        page_range = (
            f"{min(pages_list)}-{max(pages_list)}"
            if len(pages_list) > 1
            else str(pages_list[0])
            if pages_list
            else None
        )

        # Build result with enhanced metadata
        result = {"pages": pages_list, "page_range": page_range}

        # Add section metadata if available (use first/primary values to avoid duplication)
        if extraction_methods:
            result["extraction_methods"] = list(
                set(extraction_methods)
            )  # Unique values

        if section_titles:
            result["section_title"] = section_titles[
                0
            ]  # Use first (primary) section title

        if section_paths:
            result["section_path"] = section_paths[0]  # Use first (primary) path

        if all_headers:
            # Deduplicate headers by text while preserving order
            seen_texts = set()
            unique_headers = []
            for header in all_headers:
                if header["text"] not in seen_texts:
                    seen_texts.add(header["text"])
                    unique_headers.append(header)
            result["markdown_headers"] = unique_headers

        return result

    def create_vector_chunks_from_sentences(
        self,
        sentence_objects: list[dict[str, Any]],
        target_tokens: int = 300,
        max_tokens: int = 400,
        overlap_sentences: int = 3,
    ) -> list[dict[str, Any]]:
        """Create overlapping vector chunks optimized for semantic retrieval."""
        if not sentence_objects:
            return []

        chunks = []
        i = 0

        while i < len(sentence_objects):
            current_chunk_sentences = []
            total_tokens = 0

            # Add sentences until reaching target tokens
            j = i
            while j < len(sentence_objects) and total_tokens < target_tokens:
                sentence = sentence_objects[j]
                sentence_tokens = sentence["token_count"]

                # Check if adding this sentence would exceed max limit
                if total_tokens + sentence_tokens > max_tokens:
                    break

                current_chunk_sentences.append(sentence)
                total_tokens += sentence_tokens
                j += 1

            # Only create chunk if we have sentences
            if current_chunk_sentences:
                chunk_text = " ".join(s["text"] for s in current_chunk_sentences)
                sentence_ids = [s["id"] for s in current_chunk_sentences]

                # Aggregate page information and metadata
                all_pages = set()
                all_extraction_methods = []
                section_titles = []
                section_paths = []
                all_headers = []

                for sentence in current_chunk_sentences:
                    if "page_info" in sentence:
                        page_info = sentence["page_info"]

                        if page_info.get("pages"):
                            all_pages.update(page_info["pages"])

                        if page_info.get("extraction_methods"):
                            all_extraction_methods.extend(
                                page_info["extraction_methods"]
                            )

                        if page_info.get("section_title"):
                            section_titles.append(page_info["section_title"])

                        if page_info.get("section_path"):
                            section_paths.append(page_info["section_path"])

                        if page_info.get("markdown_headers"):
                            all_headers.extend(page_info["markdown_headers"])

                pages_list = sorted(list(all_pages))
                page_range = (
                    f"{min(pages_list)}-{max(pages_list)}"
                    if len(pages_list) > 1
                    else str(pages_list[0])
                    if pages_list
                    else None
                )

                # Build page_info with enhanced metadata
                page_info_dict = {"pages": pages_list, "page_range": page_range}

                if all_extraction_methods:
                    page_info_dict["extraction_methods"] = list(
                        set(all_extraction_methods)
                    )

                if section_titles:
                    page_info_dict["section_title"] = section_titles[
                        0
                    ]  # Use first/primary

                if section_paths:
                    page_info_dict["section_path"] = section_paths[
                        0
                    ]  # Use first/primary

                if all_headers:
                    # Deduplicate headers by text
                    seen_texts = set()
                    unique_headers = []
                    for header in all_headers:
                        if header["text"] not in seen_texts:
                            seen_texts.add(header["text"])
                            unique_headers.append(header)
                    page_info_dict["markdown_headers"] = unique_headers

                chunk = {
                    "id": f"vec_{len(chunks)}",
                    "type": "vector",
                    "sentence_ids": sentence_ids,
                    "text": chunk_text,
                    "token_count": total_tokens,
                    "sentence_count": len(current_chunk_sentences),
                    "sentences": current_chunk_sentences,
                    "page_info": page_info_dict,
                }
                chunks.append(chunk)

            # Move forward with overlap for next chunk
            sentences_added = j - i
            if sentences_added <= overlap_sentences:
                i += 1  # If we only added few sentences, move by 1
            else:
                i = j - overlap_sentences  # Normal overlap

        return chunks

    def create_graph_chunks_from_vector_chunks(
        self,
        vector_chunks: list[dict[str, Any]],
        chunks_per_graph: int = 5,
        overlap_chunks: int = 2,
        target_tokens: int = 1500,
    ) -> list[dict[str, Any]]:
        """Create deduplicated graph chunks from vector chunks for relationship extraction."""
        if not vector_chunks:
            return []

        graph_chunks = []
        i = 0

        while i < len(vector_chunks):
            # Select chunk group
            chunk_group = vector_chunks[i : i + chunks_per_graph]

            # Collect all unique sentence IDs
            all_sentence_ids = set()
            sentence_lookup = {}  # sentence_id -> sentence_object

            for chunk in chunk_group:
                for sentence in chunk.get("sentences", []):
                    sentence_id = sentence["id"]
                    all_sentence_ids.add(sentence_id)
                    sentence_lookup[sentence_id] = sentence

            # Sort to maintain document order
            unique_sentence_ids = sorted(list(all_sentence_ids))

            # Reconstruct deduplicated text and calculate tokens
            deduplicated_sentences = []
            total_tokens = 0

            for sentence_id in unique_sentence_ids:
                sentence = sentence_lookup[sentence_id]
                deduplicated_sentences.append(sentence)
                total_tokens += sentence["token_count"]

            deduplicated_text = " ".join(s["text"] for s in deduplicated_sentences)

            # Aggregate page information and metadata from all unique sentences
            all_pages = set()
            all_extraction_methods = []
            section_titles = []
            section_paths = []
            all_headers = []

            for sentence in deduplicated_sentences:
                if "page_info" in sentence:
                    page_info = sentence["page_info"]

                    if page_info.get("pages"):
                        all_pages.update(page_info["pages"])

                    if page_info.get("extraction_methods"):
                        all_extraction_methods.extend(page_info["extraction_methods"])

                    if page_info.get("section_title"):
                        section_titles.append(page_info["section_title"])

                    if page_info.get("section_path"):
                        section_paths.append(page_info["section_path"])

                    if page_info.get("markdown_headers"):
                        all_headers.extend(page_info["markdown_headers"])

            pages_list = sorted(list(all_pages))
            page_range = (
                f"{min(pages_list)}-{max(pages_list)}"
                if len(pages_list) > 1
                else str(pages_list[0])
                if pages_list
                else None
            )

            # Build page_info with enhanced metadata
            page_info_dict = {"pages": pages_list, "page_range": page_range}

            if all_extraction_methods:
                page_info_dict["extraction_methods"] = list(set(all_extraction_methods))

            if section_titles:
                page_info_dict["section_title"] = section_titles[0]  # Use first/primary

            if section_paths:
                page_info_dict["section_path"] = section_paths[0]  # Use first/primary

            if all_headers:
                # Deduplicate headers by text
                seen_texts = set()
                unique_headers = []
                for header in all_headers:
                    if header["text"] not in seen_texts:
                        seen_texts.add(header["text"])
                        unique_headers.append(header)
                page_info_dict["markdown_headers"] = unique_headers

            graph_chunk = {
                "id": f"graph_{len(graph_chunks)}",
                "type": "graph",
                "vector_chunk_ids": [c["id"] for c in chunk_group],
                "unique_sentence_ids": unique_sentence_ids,
                "text": deduplicated_text,  # CRITICAL FIX: Use 'text' key for storage compatibility
                "sentence_count": len(unique_sentence_ids),
                "token_count": total_tokens,
                "original_chunks_count": len(chunk_group),
                "sentences": deduplicated_sentences,
                "page_info": page_info_dict,
                "deduplication_stats": {
                    "original_sentence_count": sum(
                        len(c.get("sentences", [])) for c in chunk_group
                    ),
                    "deduplicated_sentence_count": len(unique_sentence_ids),
                    "deduplication_ratio": len(unique_sentence_ids)
                    / max(1, sum(len(c.get("sentences", [])) for c in chunk_group)),
                },
            }

            graph_chunks.append(graph_chunk)

            # Move forward with overlap
            advance = max(1, chunks_per_graph - overlap_chunks)
            i += advance

        return graph_chunks

    def process_two_tier_chunking(
        self, text: str, page_mappings: list[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Complete two-tier chunking process: sentences -> vector chunks -> graph chunks."""
        # Phase 1: Process sentences
        sentence_data = self.process_document_sentences(text, page_mappings)
        sentence_objects = sentence_data["sentences"]

        # Phase 2: Create vector chunks (300 tokens, optimized for retrieval)
        vector_chunks = self.create_vector_chunks_from_sentences(
            sentence_objects,
            target_tokens=300,
            max_tokens=self.vector_max_tokens,
            overlap_sentences=3,
        )

        # Phase 3: Create graph chunks (1500 tokens, deduplicated for analysis)
        graph_chunks = self.create_graph_chunks_from_vector_chunks(
            vector_chunks, chunks_per_graph=5, overlap_chunks=2, target_tokens=1500
        )

        return {
            "sentence_data": sentence_data,
            "vector_chunks": vector_chunks,
            "graph_chunks": graph_chunks,
            "statistics": {
                "total_sentences": sentence_data["total_sentences"],
                "total_tokens": sentence_data["total_tokens"],
                "vector_chunk_count": len(vector_chunks),
                "graph_chunk_count": len(graph_chunks),
                "avg_vector_tokens": sum(c["token_count"] for c in vector_chunks)
                / len(vector_chunks)
                if vector_chunks
                else 0,
                "avg_graph_tokens": sum(c["token_count"] for c in graph_chunks)
                / len(graph_chunks)
                if graph_chunks
                else 0,
                "deduplication_efficiency": sum(
                    c["deduplication_stats"]["deduplication_ratio"]
                    for c in graph_chunks
                )
                / len(graph_chunks)
                if graph_chunks
                else 0,
            },
        }

    def validate_two_tier_chunking(
        self, text: str, page_mappings: list[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Comprehensive validation of two-tier chunking functionality."""
        validation_results = {
            "success": True,
            "errors": [],
            "warnings": [],
            "metrics": {},
        }

        try:
            # Run two-tier chunking
            result = self.process_two_tier_chunking(text, page_mappings)

            # Validate vector chunks
            vector_validation = self._validate_vector_chunks(result["vector_chunks"])
            validation_results["metrics"]["vector"] = vector_validation

            # Validate graph chunks
            graph_validation = self._validate_graph_chunks(result["graph_chunks"])
            validation_results["metrics"]["graph"] = graph_validation

            # Validate deduplication
            dedup_validation = self._validate_deduplication(
                result["vector_chunks"], result["graph_chunks"]
            )
            validation_results["metrics"]["deduplication"] = dedup_validation

            # Validate token accuracy
            token_validation = self._validate_token_accuracy(
                result["sentence_data"]["sentences"]
            )
            validation_results["metrics"]["tokenization"] = token_validation

            # Aggregate results
            all_validations = [
                vector_validation,
                graph_validation,
                dedup_validation,
                token_validation,
            ]
            for validation in all_validations:
                validation_results["errors"].extend(validation.get("errors", []))
                validation_results["warnings"].extend(validation.get("warnings", []))

            validation_results["success"] = len(validation_results["errors"]) == 0

        except Exception as e:
            validation_results["success"] = False
            validation_results["errors"].append(
                f"Validation failed with exception: {str(e)}"
            )

        return validation_results

    def _validate_vector_chunks(
        self, vector_chunks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Validate vector chunk properties."""
        validation = {"errors": [], "warnings": [], "metrics": {}}

        if not vector_chunks:
            validation["errors"].append("No vector chunks generated")
            return validation

        token_counts = [chunk["token_count"] for chunk in vector_chunks]
        validation["metrics"] = {
            "count": len(vector_chunks),
            "avg_tokens": sum(token_counts) / len(token_counts),
            "min_tokens": min(token_counts),
            "max_tokens": max(token_counts),
            "target_utilization": sum(token_counts)
            / (len(token_counts) * self.vector_max_tokens),
        }

        # Check token limits
        oversized = [
            i for i, count in enumerate(token_counts) if count > self.vector_max_tokens
        ]
        if oversized:
            validation["errors"].append(
                f"{len(oversized)} vector chunks exceed {self.vector_max_tokens} token limit"
            )

        # Check utilization efficiency
        if validation["metrics"]["avg_tokens"] < 200:
            validation["warnings"].append(
                f"Low token utilization: avg {validation['metrics']['avg_tokens']:.1f} tokens"
            )

        return validation

    def _validate_graph_chunks(
        self, graph_chunks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Validate graph chunk properties."""
        validation = {"errors": [], "warnings": [], "metrics": {}}

        if not graph_chunks:
            validation["warnings"].append("No graph chunks generated")
            return validation

        token_counts = [chunk["token_count"] for chunk in graph_chunks]
        validation["metrics"] = {
            "count": len(graph_chunks),
            "avg_tokens": sum(token_counts) / len(token_counts),
            "min_tokens": min(token_counts),
            "max_tokens": max(token_counts),
            "avg_deduplication_ratio": sum(
                c["deduplication_stats"]["deduplication_ratio"] for c in graph_chunks
            )
            / len(graph_chunks),
        }

        # Check reasonable size for graph analysis
        if validation["metrics"]["avg_tokens"] < 1000:
            validation["warnings"].append(
                f"Graph chunks may be too small for effective analysis: avg {validation['metrics']['avg_tokens']:.1f} tokens"
            )

        return validation

    def _validate_deduplication(
        self, vector_chunks: list[dict[str, Any]], graph_chunks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Validate deduplication effectiveness."""
        validation = {"errors": [], "warnings": [], "metrics": {}}

        if not vector_chunks or not graph_chunks:
            return validation

        # Calculate deduplication metrics
        total_vector_sentences = sum(chunk["sentence_count"] for chunk in vector_chunks)
        total_graph_sentences = sum(chunk["sentence_count"] for chunk in graph_chunks)

        validation["metrics"] = {
            "total_vector_sentences": total_vector_sentences,
            "total_graph_sentences": total_graph_sentences,
            "sentence_reduction_ratio": total_graph_sentences / total_vector_sentences
            if total_vector_sentences > 0
            else 0,
        }

        # Check if deduplication is working
        if validation["metrics"]["sentence_reduction_ratio"] > 0.9:
            validation["warnings"].append(
                "Low deduplication effectiveness - graph chunks may have excessive overlap"
            )

        return validation

    def _validate_token_accuracy(
        self, sentence_objects: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Validate dual tokenizer accuracy."""
        validation = {"errors": [], "warnings": [], "metrics": {}}

        if not sentence_objects or not self.bge_tokenizer:
            return validation

        # Sample validation on first 10 sentences
        sample_sentences = sentence_objects[:10]
        divergences = []

        for sentence in sample_sentences:
            text = sentence["text"]
            openai_count = self._count_tokens_openai(text)
            bge_count = self._count_tokens_bge(text)

            if openai_count > 0:
                divergence = abs(bge_count - openai_count) / openai_count
                divergences.append(divergence)

        if divergences:
            validation["metrics"] = {
                "sample_size": len(divergences),
                "avg_divergence": sum(divergences) / len(divergences),
                "max_divergence": max(divergences),
            }

            if validation["metrics"]["max_divergence"] > 0.5:
                validation["warnings"].append(
                    f"High tokenizer divergence detected: max {validation['metrics']['max_divergence']:.2f}"
                )

        return validation

    def chunk_text_adaptive(
        self, text: str, page_mappings: list[dict[str, Any]] = None
    ):
        """Adaptive chunking that uses two-tier when enabled, traditional otherwise."""
        if self.enable_two_tier:
            return self.process_two_tier_chunking(text, page_mappings)
        else:
            # Traditional chunking for backward compatibility
            traditional_chunks = self.chunk_text(text)
            return {
                "chunks": traditional_chunks,
                "chunk_type": "traditional",
                "statistics": {
                    "chunk_count": len(traditional_chunks),
                    "avg_tokens": sum(self._count_tokens(c) for c in traditional_chunks)
                    / len(traditional_chunks)
                    if traditional_chunks
                    else 0,
                },
            }

    def _count_tokens(self, text: str) -> int:
        """Count tokens using the most conservative approach between tokenizers."""
        openai_tokens = self._count_tokens_openai(text)

        if self.bge_tokenizer:
            bge_tokens = self._count_tokens_bge(text)
            # Use the higher count for safety (conservative approach)
            return max(openai_tokens, bge_tokens)

        return openai_tokens

    def _count_tokens_openai(self, text: str) -> int:
        """Count tokens using OpenAI tokenizer."""
        try:
            return len(self.openai_tokenizer.encode(text))
        except (UnicodeDecodeError, UnicodeEncodeError, ValueError) as e:
            logger.warning(
                f"OpenAI token counting failed: {e}, using character estimation"
            )
            return len(text) // CHARS_PER_TOKEN_ESTIMATE

    def _count_tokens_bge(self, text: str) -> int:
        """Count tokens using BGE tokenizer."""
        try:
            tokens = self.bge_tokenizer(
                text, return_tensors=None, add_special_tokens=True
            )
            return len(tokens["input_ids"])
        except Exception as e:
            logger.warning(
                f"BGE token counting failed: {e}, falling back to OpenAI count"
            )
            return self._count_tokens_openai(text)

    def _check_token_safety(
        self,
        text: str,
        max_tokens: int,
        context: str,
        document_id: str = None,
        chunk_index: int = None,
    ) -> bool:
        """Check if text exceeds token limit and log warnings with full context."""
        token_count = self._count_tokens(text)

        # Build context string with all available information
        context_parts = [context]
        if document_id:
            context_parts.append(f"document_id={document_id}")
        if chunk_index is not None:
            context_parts.append(f"chunk_index={chunk_index}")
        full_context = " | ".join(context_parts)

        if token_count > max_tokens:
            # Show more text for debugging (500 chars instead of 100)
            text_preview = text[:500] + "..." if len(text) > 500 else text
            logger.error(
                f"CRITICAL: {full_context} | "
                f"Token count: {token_count}/{max_tokens} (exceeds limit by {token_count - max_tokens}) | "
                f"Text length: {len(text)} chars | "
                f"This will cause embedding failures. | "
                f"Full chunk text:\n{text_preview}"
            )
            return False
        elif token_count > max_tokens * TOKEN_WARNING_THRESHOLD:  # Warning threshold
            logger.warning(
                f"{full_context} | "
                f"Token count: {token_count}/{max_tokens} (approaching limit) | "
                f"Text length: {len(text)} chars"
            )
        return True

    def _should_finalize_chunk(
        self,
        current_chunk: list[str],
        sentence: str,
        current_length: int,
        target_sentences: int,
        max_chars: int,
        max_tokens: int,
    ) -> bool:
        """Determine if current chunk should be finalized before adding sentence."""
        if not current_chunk:
            return False

        # Test potential chunk with this sentence added
        test_chunk_text = " ".join(current_chunk + [sentence])
        test_token_count = self._count_tokens(test_chunk_text)

        # Check if adding this sentence would exceed our targets
        would_exceed_sentences = len(current_chunk) >= target_sentences
        would_exceed_chars = current_length + len(sentence) > max_chars
        would_exceed_tokens = test_token_count > max_tokens

        # Decision logic: token limit is HARD limit, others are soft
        return would_exceed_tokens or (
            would_exceed_sentences
            and (would_exceed_chars or current_length > max_chars * 0.5)
        )

    def _create_chunk_with_overlap(
        self, current_chunk: list[str], sentence: str, overlap_sentences: int
    ) -> tuple[str, list[str], int]:
        """Create chunk and return new chunk with overlap."""
        # Create the completed chunk
        chunk_text = " ".join(current_chunk)

        # Calculate overlap: take last N sentences from current chunk as start of next
        overlap_start = max(0, len(current_chunk) - overlap_sentences)
        overlap_sentences_list = current_chunk[overlap_start:]

        # Start new chunk with overlap + current sentence
        new_chunk = overlap_sentences_list + [sentence]
        new_length = sum(len(s) for s in new_chunk) + len(new_chunk) - 1

        return chunk_text, new_chunk, new_length

    def _validate_chunks_against_token_limit(
        self, chunks: list[str], max_tokens: int, document_id: str = None
    ) -> list[str]:
        """
        Validate all chunks against token limits.

        CHANGED: No longer drops oversized chunks - corrupt content should be filtered
        before chunking (Phase 0). If oversized chunks still occur, they are preserved
        with metadata for investigation rather than silent data loss.
        """
        validated_chunks = []
        for i, chunk in enumerate(chunks):
            # Check token safety but preserve all chunks
            is_safe = self._check_token_safety(
                chunk, max_tokens, "Vector RAG", document_id=document_id, chunk_index=i
            )

            # Always include the chunk (no more dropping!)
            validated_chunks.append(chunk)

            if not is_safe:
                # Log for visibility but DO NOT DROP
                text_preview = chunk[:500] + "..." if len(chunk) > 500 else chunk
                logger.error(
                    f"Oversized chunk detected (preserved with metadata) | "
                    f"document_id={document_id or 'unknown'} | "
                    f"chunk_index={i} | "
                    f"INVESTIGATE: This should have been filtered in Phase 0 | "
                    f"chunk_text:\n{text_preview}"
                )
        return validated_chunks

    def _chunk_by_sentences(
        self,
        text: str,
        target_sentences: int,
        max_chars: int,
        max_tokens: int,
        overlap_sentences: int = 1,
        document_id: str = None,
    ) -> list[str]:
        """
        Chunk text by complete sentences with intelligent overlap.
        Flexible with length to maintain semantic coherence.

        Args:
            text: Input text to chunk
            target_sentences: Target number of sentences per chunk
            max_chars: Soft character limit per chunk
            max_tokens: Hard token limit per chunk (for embedding safety)
            overlap_sentences: Number of sentences to overlap between chunks
            document_id: Optional document identifier for error logging
        """
        sentences = self._split_into_sentences(text)
        if not sentences:
            return []

        chunks = []
        current_chunk = []
        current_length = 0

        for sentence in sentences:
            sentence_length = len(sentence)

            if self._should_finalize_chunk(
                current_chunk,
                sentence,
                current_length,
                target_sentences,
                max_chars,
                max_tokens,
            ):
                # Finalize current chunk and start new one with overlap
                (
                    chunk_text,
                    current_chunk,
                    current_length,
                ) = self._create_chunk_with_overlap(
                    current_chunk, sentence, overlap_sentences
                )
                chunks.append(chunk_text)
            else:
                # Add sentence to current chunk
                current_chunk.append(sentence)
                current_length += sentence_length + 1  # +1 for space

        # Add final chunk if it has content
        if current_chunk:
            chunks.append(" ".join(current_chunk))

        # Validate all chunks against token limits
        return self._validate_chunks_against_token_limit(
            chunks, max_tokens, document_id=document_id
        )

    def chunk_text(self, text: str, document_id: str = None) -> list[str]:
        """
        Chunks text into smaller pieces for Vector RAG.
        Prioritizes complete sentences over strict character limits.
        Enforces token limits for embedding safety.

        Args:
            text: Input text to chunk
            document_id: Optional document identifier for error logging
        """
        doc_context = f" | document_id={document_id}" if document_id else ""
        logger.debug(
            f"Chunking text: {len(text)} characters, token limit: {self.vector_max_tokens}{doc_context}"
        )

        chunks = self._chunk_by_sentences(
            text,
            self.target_sentences,
            self.max_chars,
            self.vector_max_tokens,  # Hard token limit
            self.overlap_sentences,
            document_id=document_id,
        )

        # Critical verification
        token_counts = [self._count_tokens(chunk) for chunk in chunks]
        max_tokens = max(token_counts) if token_counts else 0
        oversized = sum(1 for count in token_counts if count > self.vector_max_tokens)

        logger.debug(
            f"Chunking complete: {len(chunks)} chunks, "
            f"token range: {min(token_counts) if token_counts else 0}-{max_tokens}, "
            f"limit: {self.vector_max_tokens}, oversized: {oversized}{doc_context}"
        )

        if oversized > 0:
            logger.error(
                f"CRITICAL: {oversized} chunks exceed token limit! This should not happen!{doc_context}"
            )
            for i, count in enumerate(token_counts):
                if count > self.vector_max_tokens:
                    text_preview = (
                        chunks[i][:500] + "..." if len(chunks[i]) > 500 else chunks[i]
                    )
                    logger.error(
                        f"Oversized chunk | "
                        f"document_id={document_id or 'unknown'} | "
                        f"chunk_index={i} | "
                        f"token_count={count}/{self.vector_max_tokens} | "
                        f"text_length={len(chunks[i])} chars | "
                        f"chunk_text:\n{text_preview}"
                    )

        return chunks

    def chunk_text_for_graphrag(self, text: str, document_id: str = None) -> list[str]:
        """
        Chunks text into larger pieces for GraphRAG entity extraction.
        Prioritizes semantic coherence over strict limits.
        Enforces token limits for embedding safety.

        Args:
            text: Input text to chunk
            document_id: Optional document identifier for error logging
        """
        chunks = self._chunk_by_sentences(
            text,
            self.target_sentences,
            self.max_chars,
            self.graphrag_max_tokens,  # Hard token limit
            self.overlap_sentences,
            document_id=document_id,
        )

        # Additional validation for GraphRAG chunks
        for i, chunk in enumerate(chunks):
            self._check_token_safety(
                chunk,
                self.graphrag_max_tokens,
                "GraphRAG",
                document_id=document_id,
                chunk_index=i,
            )

        return chunks

    def chunk_documents(self, documents: list[str]) -> list[str]:
        """Chunks a list of documents for Vector RAG."""
        all_chunks = []
        for doc in documents:
            all_chunks.extend(self.chunk_text(doc))
        return all_chunks

    def combine_vector_chunks_for_graphrag(
        self, vector_chunks: list[str], combine_count: int = 3
    ) -> list[str]:
        """
        Combines small vector chunks into larger chunks suitable for GraphRAG.
        This is a fallback method when we want to reuse existing vector chunks.
        """
        if not vector_chunks:
            return []

        combined_chunks = []
        for i in range(
            0, len(vector_chunks), max(1, combine_count - 1)
        ):  # Smart overlap
            chunk_group = vector_chunks[i : i + combine_count]
            combined_text = " ".join(chunk_group).strip()

            if len(combined_text) > 200:  # Minimum meaningful size
                combined_chunks.append(combined_text)

        return combined_chunks
