"""Unit tests for LLM skip-gate module."""

import pytest

from services.extraction.llm_skip_gate import (
    LLMSkipGate,
    SkipGateResult,
    _parse_decision,
    _parse_decision_from_dict,
    _sample_content,
    build_schema_summary,
)

# ── Fixtures ──


class MockLLMClient:
    """Mock LLM client that returns configurable responses."""

    def __init__(self, response=None, error=None):
        self._response = response or {"decision": "extract"}
        self._error = error
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


SAMPLE_SCHEMA = {
    "entity_type": "Company",
    "domain": "manufacturing",
    "field_groups": [
        {
            "name": "company_info",
            "description": "Basic company information",
            "fields": [
                {"name": "company_name"},
                {"name": "headquarters_location"},
                {"name": "employee_count"},
            ],
        },
        {
            "name": "products",
            "description": "Product catalog",
            "fields": [
                {"name": "product_name"},
                {"name": "series_name"},
            ],
        },
    ],
}


# ── LLMSkipGate tests ──


class TestLLMSkipGate:
    @pytest.mark.asyncio
    async def test_extract_decision(self):
        client = MockLLMClient(response={"decision": "extract"})
        gate = LLMSkipGate(llm_client=client)
        result = await gate.should_extract(
            url="https://example.com/products",
            title="Our Products",
            content="We manufacture gearboxes and drivetrain components." * 5,
            schema=SAMPLE_SCHEMA,
        )
        assert result.decision == "extract"
        assert isinstance(result, SkipGateResult)
        assert result.method == "llm_skip_gate"
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_skip_decision(self):
        client = MockLLMClient(response={"decision": "skip"})
        gate = LLMSkipGate(llm_client=client)
        result = await gate.should_extract(
            url="https://example.com/careers",
            title="Join Us",
            content="We are hiring software engineers." * 5,
            schema=SAMPLE_SCHEMA,
        )
        assert result.decision == "skip"

    @pytest.mark.asyncio
    async def test_no_schema_returns_extract(self):
        client = MockLLMClient(response={"decision": "skip"})
        gate = LLMSkipGate(llm_client=client)
        result = await gate.should_extract(
            url="https://example.com/page",
            title="Page",
            content="Some content here " * 10,
            schema={},
        )
        assert result.decision == "extract"
        assert len(client.calls) == 0  # LLM not called

    @pytest.mark.asyncio
    async def test_short_content_returns_extract(self):
        client = MockLLMClient(response={"decision": "skip"})
        gate = LLMSkipGate(llm_client=client)
        result = await gate.should_extract(
            url="https://example.com/page",
            title="Page",
            content="Short",
            schema=SAMPLE_SCHEMA,
        )
        assert result.decision == "extract"
        assert len(client.calls) == 0

    @pytest.mark.asyncio
    async def test_llm_error_returns_extract(self):
        client = MockLLMClient(error=RuntimeError("LLM timeout"))
        gate = LLMSkipGate(llm_client=client)
        result = await gate.should_extract(
            url="https://example.com/page",
            title="Page",
            content="Valid content for classification purposes." * 5,
            schema=SAMPLE_SCHEMA,
        )
        assert result.decision == "extract"
        assert result.latency > 0

    @pytest.mark.asyncio
    async def test_content_truncated(self):
        client = MockLLMClient(response={"decision": "extract"})
        gate = LLMSkipGate(llm_client=client, content_limit=100)
        long_content = "x" * 500
        await gate.should_extract(
            url="https://example.com/page",
            title="Page",
            content=long_content,
            schema=SAMPLE_SCHEMA,
        )
        # Verify content was truncated in the prompt
        call = client.calls[0]
        assert len(call["user_prompt"]) < len(long_content)

    @pytest.mark.asyncio
    async def test_latency_recorded(self):
        client = MockLLMClient(response={"decision": "extract"})
        gate = LLMSkipGate(llm_client=client)
        result = await gate.should_extract(
            url="https://example.com/page",
            title="Page",
            content="Some content for testing." * 10,
            schema=SAMPLE_SCHEMA,
        )
        assert result.latency >= 0

    @pytest.mark.asyncio
    async def test_string_response_parsed(self):
        """When LLM returns a string instead of dict, parse it."""
        client = MockLLMClient(response='{"decision": "skip"}')
        gate = LLMSkipGate(llm_client=client)
        result = await gate.should_extract(
            url="https://example.com/page",
            title="Page",
            content="Some content for testing." * 10,
            schema=SAMPLE_SCHEMA,
        )
        assert result.decision == "skip"


# ── _parse_decision tests ──


class TestParseDecision:
    def test_simple_extract(self):
        assert _parse_decision('{"decision": "extract"}') == "extract"

    def test_simple_skip(self):
        assert _parse_decision('{"decision": "skip"}') == "skip"

    def test_thinking_tags(self):
        text = '<think>This is a careers page</think>{"decision":"skip"}'
        assert _parse_decision(text) == "skip"

    def test_markdown_fences(self):
        text = '```json\n{"decision":"skip"}\n```'
        assert _parse_decision(text) == "skip"

    def test_garbled_returns_extract(self):
        assert _parse_decision("garbled nonsense output") == "extract"

    def test_empty_returns_extract(self):
        assert _parse_decision("") == "extract"

    def test_keyword_fallback_skip(self):
        assert _parse_decision('The answer is "skip"') == "skip"

    def test_keyword_fallback_extract(self):
        assert _parse_decision("The answer is extract") == "extract"

    def test_uppercase_skip(self):
        assert _parse_decision('{"decision": "SKIP"}') == "skip"

    def test_extra_whitespace(self):
        assert _parse_decision('  {"decision":  "skip" }  ') == "skip"


class TestParseDecisionFromDict:
    def test_extract(self):
        assert _parse_decision_from_dict({"decision": "extract"}) == "extract"

    def test_skip(self):
        assert _parse_decision_from_dict({"decision": "skip"}) == "skip"

    def test_missing_key(self):
        assert _parse_decision_from_dict({"foo": "bar"}) == "extract"

    def test_non_string_value(self):
        assert _parse_decision_from_dict({"decision": 123}) == "extract"


# ── build_schema_summary tests ──


class TestBuildSchemaSummary:
    def test_full_schema(self):
        summary = build_schema_summary(SAMPLE_SCHEMA)
        assert "manufacturing" in summary
        assert "Company" in summary
        assert "company_info" in summary
        assert "products" in summary
        assert "company_name" in summary
        assert "product_name" in summary

    def test_empty_schema(self):
        assert build_schema_summary({}) == ""

    def test_no_field_groups(self):
        assert build_schema_summary({"entity_type": "Foo"}) == ""

    def test_minimal_field_group(self):
        schema = {"field_groups": [{"name": "basic"}]}
        summary = build_schema_summary(schema)
        assert "basic" in summary

    def test_field_group_with_description(self):
        schema = {
            "field_groups": [
                {"name": "info", "description": "General information"},
            ]
        }
        summary = build_schema_summary(schema)
        assert "info: General information" in summary

    def test_limits_key_fields(self):
        schema = {
            "field_groups": [
                {
                    "name": "many_fields",
                    "fields": [{"name": f"field_{i}"} for i in range(10)],
                },
            ],
        }
        summary = build_schema_summary(schema)
        # Should only show first 4
        assert "field_0" in summary
        assert "field_3" in summary
        assert "field_4" not in summary


# ── _sample_content tests ──


class TestSampleContent:
    def test_short_content_unchanged(self):
        content = "short text"
        assert _sample_content(content, 2000) == content

    def test_exact_limit_unchanged(self):
        content = "x" * 2000
        assert _sample_content(content, 2000) == content

    def test_long_content_sampled(self):
        # Beginning has "START", middle has "MIDDLE", end has "END"
        content = "START" + ("a" * 5000) + "MIDDLE" + ("b" * 5000) + "END"
        sampled = _sample_content(content, 2000)
        assert len(sampled) < len(content)
        assert sampled.startswith("START")
        assert "END" in sampled
        assert "[...]" in sampled

    def test_middle_section_captured(self):
        # Content where only the middle has useful data
        nav = "Navigation menu link1 link2 link3 " * 50  # ~1700 chars
        data = "PRODUCT GEARBOX 500NM TORQUE RATING " * 50  # ~1800 chars
        footer = "Copyright 2024 legal privacy " * 50  # ~1450 chars
        content = nav + data + footer
        sampled = _sample_content(content, 2000)
        # Middle sample should capture some product data
        assert "GEARBOX" in sampled or "TORQUE" in sampled

    def test_has_three_sections(self):
        content = "x" * 10000
        sampled = _sample_content(content, 2000)
        assert sampled.count("[...]") == 2
