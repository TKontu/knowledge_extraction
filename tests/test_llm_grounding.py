"""Tests for LLM-based grounding verification."""

from unittest.mock import AsyncMock

import pytest

from services.extraction.llm_grounding import LLMGroundingResult, LLMGroundingVerifier


@pytest.fixture
def mock_llm_client():
    client = AsyncMock()
    client.model = "Qwen3-30B-A3B-it-4bit"
    return client


@pytest.fixture
def verifier(mock_llm_client):
    return LLMGroundingVerifier(llm_client=mock_llm_client)


class TestVerifyQuote:
    @pytest.mark.asyncio
    async def test_supported_numeric(self, verifier, mock_llm_client):
        """LLM confirms quote supports the claimed value."""
        mock_llm_client.complete.return_value = {
            "supported": True,
            "reason": "Quote explicitly states 140,000 employees",
        }
        result = await verifier.verify_quote(
            "employee_count", 140000, "approximately 140,000 employees worldwide"
        )
        assert result.supported is True
        assert result.latency >= 0

    @pytest.mark.asyncio
    async def test_rejected_wrong_number(self, verifier, mock_llm_client):
        """LLM rejects when quote number doesn't match claimed value."""
        mock_llm_client.complete.return_value = {
            "supported": False,
            "reason": "Quote says 35 employees, not 5000",
        }
        result = await verifier.verify_quote(
            "employee_count", 5000, "The company has 35 employees"
        )
        assert result.supported is False

    @pytest.mark.asyncio
    async def test_rejected_unit_conversion(self, verifier, mock_llm_client):
        """LLM rejects unit conversion hallucinations."""
        mock_llm_client.complete.return_value = {
            "supported": False,
            "reason": "Quote says 40HP which is not the same as 29.8kW",
        }
        result = await verifier.verify_quote(
            "power_rating_kw", 29.8, "rated at 40HP output"
        )
        assert result.supported is False

    @pytest.mark.asyncio
    async def test_rejected_wrong_category(self, verifier, mock_llm_client):
        """LLM rejects when quote describes a different metric."""
        mock_llm_client.complete.return_value = {
            "supported": False,
            "reason": "Quote describes revenue, not employee count",
        }
        result = await verifier.verify_quote(
            "employee_count", 1000, "revenue of €1 Billion"
        )
        assert result.supported is False

    @pytest.mark.asyncio
    async def test_multilingual_supported(self, verifier, mock_llm_client):
        """LLM understands multilingual quotes."""
        mock_llm_client.complete.return_value = {
            "supported": True,
            "reason": "Portuguese '30 mil' means 30,000",
        }
        result = await verifier.verify_quote(
            "employee_count", 30000, "30 mil colaboradores"
        )
        assert result.supported is True

    @pytest.mark.asyncio
    async def test_llm_error_returns_none(self, verifier, mock_llm_client):
        """Timeout or LLM error returns supported=None."""
        mock_llm_client.complete.side_effect = Exception("LLM timeout")
        result = await verifier.verify_quote(
            "employee_count", 140000, "approximately 140,000 employees"
        )
        assert result.supported is None
        assert "LLM timeout" in result.reason

    @pytest.mark.asyncio
    async def test_malformed_response_returns_none(self, verifier, mock_llm_client):
        """Unparseable LLM response returns supported=None."""
        mock_llm_client.complete.return_value = {"gibberish": True}
        result = await verifier.verify_quote(
            "employee_count", 140000, "approximately 140,000 employees"
        )
        assert result.supported is None

    @pytest.mark.asyncio
    async def test_prompt_includes_field_and_value(self, verifier, mock_llm_client):
        """Verify the prompt sent to LLM contains field name, value, and quote."""
        mock_llm_client.complete.return_value = {
            "supported": True,
            "reason": "confirmed",
        }
        await verifier.verify_quote("employee_count", 5000, "has 5000 workers")

        call_args = mock_llm_client.complete.call_args
        # Check that the prompt contains the key information
        assert "employee_count" in str(call_args)
        assert "5000" in str(call_args)
        assert "5000 workers" in str(call_args)


class TestVerifyExtraction:
    @pytest.mark.asyncio
    async def test_only_verifies_score_zero(self, verifier, mock_llm_client):
        """Fields with score > 0 are not sent to LLM."""
        mock_llm_client.complete.return_value = {
            "supported": True,
            "reason": "confirmed",
        }
        data = {
            "company_name": "ABB",
            "employee_count": 105000,
            "_quotes": {
                "company_name": "ABB is a leader",
                "employee_count": "more than 140-year history",
            },
        }
        grounding_scores = {"company_name": 1.0, "employee_count": 0.0}
        field_types = {"company_name": "string", "employee_count": "integer"}

        updated = await verifier.verify_extraction(data, grounding_scores, field_types)

        # Only employee_count should have been verified (was 0.0)
        assert mock_llm_client.complete.call_count == 1
        # company_name should remain unchanged
        assert updated["company_name"] == 1.0

    @pytest.mark.asyncio
    async def test_all_grounded_no_llm_calls(self, verifier, mock_llm_client):
        """When all scores are >= 0.5, no LLM calls are made."""
        data = {
            "company_name": "ABB",
            "_quotes": {"company_name": "ABB Corp"},
        }
        grounding_scores = {"company_name": 1.0}
        field_types = {"company_name": "string"}

        await verifier.verify_extraction(data, grounding_scores, field_types)
        mock_llm_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_quote_skipped(self, verifier, mock_llm_client):
        """Fields without quotes are not sent to LLM."""
        data = {
            "employee_count": 5000,
            "_quotes": {},
        }
        grounding_scores = {"employee_count": 0.0}
        field_types = {"employee_count": "integer"}

        await verifier.verify_extraction(data, grounding_scores, field_types)
        mock_llm_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_boolean_skipped(self, verifier, mock_llm_client):
        """Boolean fields are not sent to LLM (too strict, 35% false rejection)."""
        data = {
            "manufactures_gears": True,
            "_quotes": {"manufactures_gears": "produces gears"},
        }
        grounding_scores = {"manufactures_gears": 0.0}
        field_types = {"manufactures_gears": "boolean"}

        await verifier.verify_extraction(data, grounding_scores, field_types)
        mock_llm_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_scores_on_success(self, verifier, mock_llm_client):
        """Successful LLM verification updates the score to 1.0."""
        mock_llm_client.complete.return_value = {
            "supported": True,
            "reason": "confirmed",
        }
        data = {
            "employee_count": 30000,
            "_quotes": {"employee_count": "30 mil colaboradores"},
        }
        grounding_scores = {"employee_count": 0.0}
        field_types = {"employee_count": "integer"}

        updated = await verifier.verify_extraction(data, grounding_scores, field_types)
        assert updated["employee_count"] == 1.0

    @pytest.mark.asyncio
    async def test_keeps_zero_on_rejection(self, verifier, mock_llm_client):
        """Rejected LLM verification keeps score at 0.0."""
        mock_llm_client.complete.return_value = {
            "supported": False,
            "reason": "quote doesn't support value",
        }
        data = {
            "employee_count": 140000,
            "_quotes": {"employee_count": "140-year history"},
        }
        grounding_scores = {"employee_count": 0.0}
        field_types = {"employee_count": "integer"}

        updated = await verifier.verify_extraction(data, grounding_scores, field_types)
        assert updated["employee_count"] == 0.0

    @pytest.mark.asyncio
    async def test_keeps_score_on_error(self, verifier, mock_llm_client):
        """LLM error leaves score unchanged."""
        mock_llm_client.complete.side_effect = Exception("timeout")
        data = {
            "employee_count": 140000,
            "_quotes": {"employee_count": "about 140,000"},
        }
        grounding_scores = {"employee_count": 0.0}
        field_types = {"employee_count": "integer"}

        updated = await verifier.verify_extraction(data, grounding_scores, field_types)
        assert updated["employee_count"] == 0.0

    @pytest.mark.asyncio
    async def test_mixed_scores(self, verifier, mock_llm_client):
        """Mix of grounded, ungrounded, and LLM-verified fields."""
        mock_llm_client.complete.return_value = {
            "supported": True,
            "reason": "Finnish confirms 147 employees",
        }
        data = {
            "company_name": "Atagears",
            "employee_count": 147,
            "headquarters_location": "Helsinki",
            "_quotes": {
                "company_name": "Atagears Oy",
                "employee_count": "yli 140 voimansiirron ammattilaista",
                "headquarters_location": "based in Helsinki, Finland",
            },
        }
        grounding_scores = {
            "company_name": 1.0,
            "employee_count": 0.0,
            "headquarters_location": 1.0,
        }
        field_types = {
            "company_name": "string",
            "employee_count": "integer",
            "headquarters_location": "string",
        }

        updated = await verifier.verify_extraction(data, grounding_scores, field_types)
        assert updated["company_name"] == 1.0  # unchanged
        assert updated["employee_count"] == 1.0  # LLM confirmed
        assert updated["headquarters_location"] == 1.0  # unchanged
        assert mock_llm_client.complete.call_count == 1  # only employee_count


class TestLLMGroundingResult:
    def test_frozen(self):
        r = LLMGroundingResult(supported=True, reason="ok", latency=0.5)
        with pytest.raises(AttributeError):
            r.supported = False

    def test_fields(self):
        r = LLMGroundingResult(supported=None, reason="error", latency=1.2)
        assert r.supported is None
        assert r.reason == "error"
        assert r.latency == 1.2


class TestNonBoolSupportedHandling:
    """M4: Non-bool 'supported' values are treated as malformed."""

    @pytest.fixture
    def mock_llm_client(self):
        from unittest.mock import AsyncMock

        return AsyncMock()

    @pytest.fixture
    def verifier(self, mock_llm_client):
        return LLMGroundingVerifier(llm_client=mock_llm_client)

    @pytest.mark.asyncio
    async def test_string_supported_treated_as_malformed(self, verifier, mock_llm_client):
        mock_llm_client.complete.return_value = {"supported": "yes", "reason": "looks good"}
        result = await verifier.verify_quote("name", "ABB", "ABB Corp")
        assert result.supported is None
        assert "Malformed" in result.reason

    @pytest.mark.asyncio
    async def test_int_supported_treated_as_malformed(self, verifier, mock_llm_client):
        mock_llm_client.complete.return_value = {"supported": 1, "reason": "confirmed"}
        result = await verifier.verify_quote("name", "ABB", "ABB Corp")
        assert result.supported is None


class TestNonStringQuoteCoercion:
    """Verify that non-string quotes in _quotes dict don't crash LLM verification."""

    @pytest.fixture
    def mock_llm_client(self):
        from unittest.mock import AsyncMock

        return AsyncMock()

    @pytest.fixture
    def verifier(self, mock_llm_client):
        return LLMGroundingVerifier(llm_client=mock_llm_client)

    @pytest.mark.asyncio
    async def test_list_quote_coerced_to_string(self, verifier, mock_llm_client):
        """A list quote is joined into a string before sending to LLM."""
        mock_llm_client.complete.return_value = {
            "supported": True,
            "reason": "confirmed",
        }
        data = {
            "employee_count": 5000,
            "_quotes": {"employee_count": ["about 5,000 employees", "worldwide"]},
        }
        grounding_scores = {"employee_count": 0.0}
        field_types = {"employee_count": "integer"}

        updated = await verifier.verify_extraction(data, grounding_scores, field_types)
        # Should have called LLM with the coerced string, not crashed
        assert mock_llm_client.complete.call_count == 1
        assert updated["employee_count"] == 1.0

    @pytest.mark.asyncio
    async def test_none_quote_skipped(self, verifier, mock_llm_client):
        """None quote is skipped (no LLM call)."""
        data = {
            "employee_count": 5000,
            "_quotes": {"employee_count": None},
        }
        grounding_scores = {"employee_count": 0.0}
        field_types = {"employee_count": "integer"}

        await verifier.verify_extraction(data, grounding_scores, field_types)
        mock_llm_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_list_quote_skipped(self, verifier, mock_llm_client):
        """Empty list quote is skipped (no LLM call)."""
        data = {
            "employee_count": 5000,
            "_quotes": {"employee_count": []},
        }
        grounding_scores = {"employee_count": 0.0}
        field_types = {"employee_count": "integer"}

        await verifier.verify_extraction(data, grounding_scores, field_types)
        mock_llm_client.complete.assert_not_called()
