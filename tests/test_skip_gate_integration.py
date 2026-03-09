"""Integration tests for LLM skip-gate in the orchestrator flow."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.llm_skip_gate import LLMSkipGate
from services.extraction.page_classifier import ClassificationMethod
from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator


@dataclass(frozen=True)
class FakeClassificationConfig:
    enabled: bool = True
    skip_enabled: bool = True
    smart_enabled: bool = False
    skip_gate_enabled: bool = True
    skip_gate_model: str = ""
    skip_gate_content_limit: int = 2000
    reranker_model: str = ""
    embedding_high_threshold: float = 0.7
    embedding_low_threshold: float = 0.3
    reranker_threshold: float = 0.5
    cache_ttl: int = 300
    use_default_skip_patterns: bool = True
    classifier_content_limit: int = 6000


@pytest.fixture
def sample_field_groups():
    return [
        FieldGroup(
            name="company_info",
            description="Company information",
            fields=[
                FieldDefinition(name="company_name", field_type="text", description="Name"),
            ],
            prompt_hint="Extract company info",
        ),
        FieldGroup(
            name="products",
            description="Product catalog",
            fields=[
                FieldDefinition(name="product_name", field_type="text", description="Product"),
            ],
            prompt_hint="Extract products",
        ),
    ]


@pytest.fixture
def mock_extractor():
    extractor = Mock()
    extractor.extract_field_group = AsyncMock(return_value={"test": "data"})
    return extractor


@pytest.fixture
def sample_schema():
    return {
        "entity_type": "Company",
        "domain": "manufacturing",
        "field_groups": [
            {"name": "company_info", "description": "Company information"},
            {"name": "products", "description": "Product catalog"},
        ],
    }


class TestSkipGateIntegration:
    @pytest.mark.asyncio
    async def test_rule_skip_still_works(self, mock_extractor, sample_field_groups):
        """Rule-based skip (URL patterns) still works without skip-gate."""
        config = FakeClassificationConfig(skip_gate_enabled=False)
        orchestrator = SchemaExtractionOrchestrator(
            mock_extractor,
            classification_config=config,
        )

        results, classification = await orchestrator.extract_all_groups(
            source_id=uuid4(),
            markdown="# Careers\nJoin our team!",
            source_context="Test Company",
            field_groups=sample_field_groups,
            source_url="https://example.com/careers",
            source_title="Careers",
        )

        assert results == []
        assert classification is not None
        assert classification.skip_extraction is True

    @pytest.mark.asyncio
    async def test_skip_gate_skips_page(
        self, mock_extractor, sample_field_groups, sample_schema,
    ):
        """Skip-gate returns 'skip' → extraction not called."""
        mock_llm = MockLLMClient(response={"decision": "skip"})
        skip_gate = LLMSkipGate(llm_client=mock_llm, content_limit=2000)
        config = FakeClassificationConfig(skip_gate_enabled=True)

        orchestrator = SchemaExtractionOrchestrator(
            mock_extractor,
            classification_config=config,
            skip_gate=skip_gate,
            extraction_schema=sample_schema,
        )

        results, classification = await orchestrator.extract_all_groups(
            source_id=uuid4(),
            markdown="This is a random blog post about cooking." * 10,
            source_context="Test Company",
            field_groups=sample_field_groups,
            source_url="https://example.com/blog/cooking",
            source_title="Cooking Tips",
        )

        assert results == []
        assert classification is not None
        assert classification.skip_extraction is True
        assert classification.method == ClassificationMethod.LLM
        assert "skip-gate" in (classification.reasoning or "")
        mock_extractor.extract_field_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_gate_extract_runs_all_groups(
        self, mock_extractor, sample_field_groups, sample_schema,
    ):
        """Skip-gate returns 'extract' → all groups processed."""
        mock_llm = MockLLMClient(response={"decision": "extract"})
        skip_gate = LLMSkipGate(llm_client=mock_llm, content_limit=2000)
        config = FakeClassificationConfig(skip_gate_enabled=True)

        orchestrator = SchemaExtractionOrchestrator(
            mock_extractor,
            classification_config=config,
            skip_gate=skip_gate,
            extraction_schema=sample_schema,
        )

        results, classification = await orchestrator.extract_all_groups(
            source_id=uuid4(),
            markdown="We manufacture gearboxes and drivetrain components." * 10,
            source_context="Test Company",
            field_groups=sample_field_groups,
            source_url="https://example.com/products",
            source_title="Products",
        )

        assert classification is not None
        assert classification.skip_extraction is False
        assert classification.method == ClassificationMethod.LLM
        # All groups should be in relevant_groups
        assert set(classification.relevant_groups) == {"company_info", "products"}
        # Extractor should have been called (at least once per group)
        assert mock_extractor.extract_field_group.call_count >= 2

    @pytest.mark.asyncio
    async def test_skip_gate_disabled_normal_flow(
        self, mock_extractor, sample_field_groups, sample_schema,
    ):
        """skip_gate_enabled=False → gate not called, normal flow."""
        mock_llm = MockLLMClient(response={"decision": "skip"})
        skip_gate = LLMSkipGate(llm_client=mock_llm, content_limit=2000)
        config = FakeClassificationConfig(skip_gate_enabled=False)

        orchestrator = SchemaExtractionOrchestrator(
            mock_extractor,
            classification_config=config,
            skip_gate=skip_gate,
            extraction_schema=sample_schema,
        )

        results, classification = await orchestrator.extract_all_groups(
            source_id=uuid4(),
            markdown="Product gearbox 500NM torque." * 10,
            source_context="Test Company",
            field_groups=sample_field_groups,
            source_url="https://example.com/products",
            source_title="Products",
        )

        # Gate should NOT have been called
        assert len(mock_llm.calls) == 0
        # Extraction should proceed
        assert mock_extractor.extract_field_group.call_count >= 1

    @pytest.mark.asyncio
    async def test_skip_gate_failure_extracts(
        self, mock_extractor, sample_field_groups, sample_schema,
    ):
        """Skip-gate raises error → extraction proceeds (safe default)."""
        mock_llm = MockLLMClient(error=RuntimeError("LLM down"))
        skip_gate = LLMSkipGate(llm_client=mock_llm, content_limit=2000)
        config = FakeClassificationConfig(skip_gate_enabled=True)

        orchestrator = SchemaExtractionOrchestrator(
            mock_extractor,
            classification_config=config,
            skip_gate=skip_gate,
            extraction_schema=sample_schema,
        )

        results, classification = await orchestrator.extract_all_groups(
            source_id=uuid4(),
            markdown="Products and services page content." * 10,
            source_context="Test Company",
            field_groups=sample_field_groups,
            source_url="https://example.com/products",
            source_title="Products",
        )

        # Should have defaulted to "extract" and proceeded
        assert classification is not None
        assert classification.skip_extraction is False
        assert mock_extractor.extract_field_group.call_count >= 1

    @pytest.mark.asyncio
    async def test_smart_classifier_fallback(
        self, mock_extractor, sample_field_groups, sample_schema,
    ):
        """skip-gate disabled + smart enabled → smart classifier used."""
        from services.extraction.page_classifier import ClassificationResult

        mock_smart = AsyncMock()
        mock_smart.classify = AsyncMock(
            return_value=ClassificationResult(
                page_type="product",
                relevant_groups=["products"],
                skip_extraction=False,
                confidence=0.9,
                method=ClassificationMethod.HYBRID,
            )
        )
        config = FakeClassificationConfig(
            skip_gate_enabled=False, smart_enabled=True,
        )

        orchestrator = SchemaExtractionOrchestrator(
            mock_extractor,
            classification_config=config,
            smart_classifier=mock_smart,
            extraction_schema=sample_schema,
        )

        results, classification = await orchestrator.extract_all_groups(
            source_id=uuid4(),
            markdown="Product specs and details." * 10,
            source_context="Test Company",
            field_groups=sample_field_groups,
            source_url="https://example.com/products",
            source_title="Products",
        )

        assert classification is not None
        assert classification.method == ClassificationMethod.HYBRID
        mock_smart.classify.assert_called_once()


class MockLLMClient:
    """Mock LLM client for integration tests."""

    def __init__(self, response=None, error=None):
        self._response = response or {"decision": "extract"}
        self._error = error
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response
