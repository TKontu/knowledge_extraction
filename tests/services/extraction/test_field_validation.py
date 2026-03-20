"""Tests for FieldValidationService."""

import httpx
import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup, ValidatorSpec
from services.extraction.field_validation import FieldValidationService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COUNTRY_SET = frozenset(
    {
        "germany",
        "france",
        "united states",
        "china",
        "czech republic",
        "poland",
    }
)

CITY_SET = frozenset({"munich", "berlin", "paris", "prague", "shanghai"})


def _svc() -> FieldValidationService:
    return FieldValidationService(
        factapi_url="http://fakefactapi.invalid",
        api_key="test",
    )


def _city_field(action: str = "nullify") -> FieldDefinition:
    return FieldDefinition(
        name="city",
        field_type="text",
        description="City name",
        validators=[
            ValidatorSpec(
                type="factapi_not_in_column",
                collection="worldcities",
                column="country",
                action=action,
            )
        ],
    )


def _lookup_sets(case_sensitive: bool = False) -> dict:
    key = f"worldcities/country/{'cs' if case_sensitive else 'ci'}"
    return {key: COUNTRY_SET}


# ---------------------------------------------------------------------------
# apply_to_entity_fields
# ---------------------------------------------------------------------------


class TestApplyToEntityFields:
    def test_not_in_column_nullifies_country_in_city(self) -> None:
        svc = _svc()
        fields = {"city": "Germany", "country": None}
        result, violations = svc.apply_to_entity_fields(
            fields, [_city_field()], _lookup_sets()
        )
        assert result["city"] is None
        assert len(violations) == 1
        assert violations[0]["field"] == "city"
        assert violations[0]["action"] == "nullify"

    def test_not_in_column_keeps_real_city(self) -> None:
        svc = _svc()
        fields = {"city": "Munich", "country": "Germany"}
        result, violations = svc.apply_to_entity_fields(
            fields, [_city_field()], _lookup_sets()
        )
        assert result["city"] == "Munich"
        assert violations == []

    def test_case_insensitive_match(self) -> None:
        svc = _svc()
        fields = {"city": "GERMANY"}
        result, violations = svc.apply_to_entity_fields(
            fields, [_city_field()], _lookup_sets()
        )
        assert result["city"] is None
        assert len(violations) == 1

    def test_action_warn_keeps_value(self) -> None:
        svc = _svc()
        fields = {"city": "France"}
        result, violations = svc.apply_to_entity_fields(
            fields, [_city_field(action="warn")], _lookup_sets()
        )
        assert result["city"] == "France"  # kept
        assert len(violations) == 1
        assert violations[0]["action"] == "warn"

    def test_none_value_skipped(self) -> None:
        svc = _svc()
        fields = {"city": None}
        result, violations = svc.apply_to_entity_fields(
            fields, [_city_field()], _lookup_sets()
        )
        assert result["city"] is None
        assert violations == []

    def test_empty_string_skipped(self) -> None:
        svc = _svc()
        fields = {"city": ""}
        result, violations = svc.apply_to_entity_fields(
            fields, [_city_field()], _lookup_sets()
        )
        assert result["city"] == ""
        assert violations == []

    def test_missing_lookup_set_skips_validator(self) -> None:
        svc = _svc()
        fields = {"city": "Germany"}
        # Empty lookup_sets — key absent
        result, violations = svc.apply_to_entity_fields(fields, [_city_field()], {})
        assert result["city"] == "Germany"
        assert violations == []

    def test_none_lookup_set_skips_validator(self) -> None:
        svc = _svc()
        fields = {"city": "Germany"}
        result, violations = svc.apply_to_entity_fields(
            fields, [_city_field()], {"worldcities/country/ci": None}
        )
        assert result["city"] == "Germany"
        assert violations == []

    def test_exists_in_column_violation(self) -> None:
        """factapi_exists_in_column: violation when value NOT in set."""
        fdef = FieldDefinition(
            name="city",
            field_type="text",
            description="City",
            validators=[
                ValidatorSpec(
                    type="factapi_exists_in_column",
                    collection="worldcities",
                    column="city",
                    action="nullify",
                )
            ],
        )
        svc = _svc()
        lookup_sets = {"worldcities/city/ci": CITY_SET}
        # "Atlantis" not in CITY_SET → violation
        fields = {"city": "Atlantis"}
        result, violations = svc.apply_to_entity_fields(fields, [fdef], lookup_sets)
        assert result["city"] is None
        assert len(violations) == 1

    def test_exists_in_column_no_violation(self) -> None:
        """factapi_exists_in_column: no violation when value IS in set."""
        fdef = FieldDefinition(
            name="city",
            field_type="text",
            description="City",
            validators=[
                ValidatorSpec(
                    type="factapi_exists_in_column",
                    collection="worldcities",
                    column="city",
                    action="nullify",
                )
            ],
        )
        svc = _svc()
        lookup_sets = {"worldcities/city/ci": CITY_SET}
        fields = {"city": "Munich"}
        result, violations = svc.apply_to_entity_fields(fields, [fdef], lookup_sets)
        assert result["city"] == "Munich"
        assert violations == []


# ---------------------------------------------------------------------------
# get_lookup_set — network behaviour
# ---------------------------------------------------------------------------


class TestGetLookupSet:
    @pytest.mark.asyncio
    async def test_factapi_unavailable_returns_none(self, monkeypatch) -> None:
        svc = _svc()

        async def mock_get(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        result = await svc.get_lookup_set("worldcities", "country")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_avoids_refetch(self, monkeypatch) -> None:
        svc = _svc()
        call_count = 0

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"country": "Germany"}, {"country": "France"}]}

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return FakeResp()

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

        result1 = await svc.get_lookup_set("worldcities", "country")
        result2 = await svc.get_lookup_set("worldcities", "country")

        assert result1 == result2
        assert call_count == 1  # fetched once only

    @pytest.mark.asyncio
    async def test_successful_fetch_lowercases_by_default(self, monkeypatch) -> None:
        svc = _svc()

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"country": "Germany"}, {"country": "FRANCE"}]}

        async def mock_get(*args, **kwargs):
            return FakeResp()

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

        result = await svc.get_lookup_set("worldcities", "country")
        assert result is not None
        assert "germany" in result
        assert "france" in result
        # Original casing should NOT be present
        assert "Germany" not in result


# ---------------------------------------------------------------------------
# apply_to_result — v1 and v2 formats
# ---------------------------------------------------------------------------


def _location_group() -> FieldGroup:
    return FieldGroup(
        name="company_locations",
        description="Locations",
        fields=[
            _city_field(),
            FieldDefinition(name="country", field_type="text", description="Country"),
        ],
        prompt_hint="",
        is_entity_list=True,
    )


class TestApplyToResult:
    def test_v1_entity_list_applied(self) -> None:
        svc = _svc()
        group = _location_group()
        result_data = {
            "company_locations": [
                {"city": "Germany", "country": "Germany"},
                {"city": "Munich", "country": "Germany"},
            ]
        }
        modified, violations = svc.apply_to_result(result_data, group, _lookup_sets())
        items = modified["company_locations"]
        assert items[0]["city"] is None  # "Germany" → nullified
        assert items[1]["city"] == "Munich"  # real city → kept
        assert len(violations) == 1

    def test_v2_entity_list_applied(self) -> None:
        svc = _svc()
        group = _location_group()
        result_data = {
            "company_locations": {
                "items": [
                    {
                        "fields": {"city": "France", "country": "France"},
                        "confidence": 0.9,
                    },
                    {
                        "fields": {"city": "Paris", "country": "France"},
                        "confidence": 0.8,
                    },
                ]
            }
        }
        modified, violations = svc.apply_to_result(result_data, group, _lookup_sets())
        items = modified["company_locations"]["items"]
        assert items[0]["fields"]["city"] is None  # "France" nullified
        assert items[1]["fields"]["city"] == "Paris"  # kept
        assert len(violations) == 1


# ---------------------------------------------------------------------------
# factapi_fill_from_lookup
# ---------------------------------------------------------------------------

# Munich → uniquely Germany; San Jose → United States + Costa Rica (ambiguous)
CITY_COUNTRY_MAPPING: dict[str, frozenset[str]] = {
    "munich": frozenset({"Germany"}),
    "berlin": frozenset({"Germany"}),
    "paris": frozenset({"France"}),
    "san jose": frozenset({"United States", "Costa Rica"}),
}

_MAPPING_KEY = "worldcities/city\u2192country/ci"


def _fill_spec(action: str = "fill_if_null", unique_only: bool = True) -> ValidatorSpec:
    return ValidatorSpec(
        type="factapi_fill_from_lookup",
        collection="worldcities",
        column="city",
        fill_column="country",
        target_field="country",
        action=action,
        unique_only=unique_only,
    )


def _fill_lookup_sets() -> dict:
    return {_MAPPING_KEY: CITY_COUNTRY_MAPPING}


def _city_field_with_both_validators() -> FieldDefinition:
    """city field: first nullify misplaced countries, then fill country from city."""
    return FieldDefinition(
        name="city",
        field_type="text",
        description="City name",
        validators=[
            ValidatorSpec(
                type="factapi_not_in_column",
                collection="worldcities",
                column="country",
                action="nullify",
            ),
            _fill_spec(),
        ],
    )


def _combined_lookup_sets() -> dict:
    """Both the country lookup set and the city→country mapping."""
    return {**_lookup_sets(), **_fill_lookup_sets()}


class TestFillFromLookup:
    def test_fill_unique_city_fills_country(self) -> None:
        svc = _svc()
        fdef = FieldDefinition(
            name="city",
            field_type="text",
            description="City",
            validators=[_fill_spec()],
        )
        fields = {"city": "Munich", "country": None}
        result, changes = svc.apply_to_entity_fields(
            fields, [fdef], _fill_lookup_sets()
        )
        assert result["country"] == "Germany"
        assert len(changes) == 1
        assert changes[0]["action"] == "fill_if_null"
        assert changes[0]["filled_value"] == "Germany"

    def test_fill_ambiguous_city_skips(self) -> None:
        svc = _svc()
        fdef = FieldDefinition(
            name="city",
            field_type="text",
            description="City",
            validators=[_fill_spec(unique_only=True)],
        )
        fields = {"city": "San Jose", "country": None}
        result, changes = svc.apply_to_entity_fields(
            fields, [fdef], _fill_lookup_sets()
        )
        assert result["country"] is None
        assert changes == []

    def test_fill_if_null_skips_when_country_present(self) -> None:
        svc = _svc()
        fdef = FieldDefinition(
            name="city",
            field_type="text",
            description="City",
            validators=[_fill_spec(action="fill_if_null")],
        )
        fields = {"city": "Munich", "country": "France"}
        result, changes = svc.apply_to_entity_fields(
            fields, [fdef], _fill_lookup_sets()
        )
        assert result["country"] == "France"  # unchanged
        assert changes == []

    def test_fill_always_overwrites(self) -> None:
        svc = _svc()
        fdef = FieldDefinition(
            name="city",
            field_type="text",
            description="City",
            validators=[_fill_spec(action="fill_always")],
        )
        fields = {"city": "Munich", "country": "France"}
        result, changes = svc.apply_to_entity_fields(
            fields, [fdef], _fill_lookup_sets()
        )
        assert result["country"] == "Germany"
        assert len(changes) == 1

    def test_fill_city_not_in_mapping_skips(self) -> None:
        svc = _svc()
        fdef = FieldDefinition(
            name="city",
            field_type="text",
            description="City",
            validators=[_fill_spec()],
        )
        fields = {"city": "Atlantis", "country": None}
        result, changes = svc.apply_to_entity_fields(
            fields, [fdef], _fill_lookup_sets()
        )
        assert result["country"] is None
        assert changes == []

    def test_fill_unavailable_factapi_skips(self) -> None:
        svc = _svc()
        fdef = FieldDefinition(
            name="city",
            field_type="text",
            description="City",
            validators=[_fill_spec()],
        )
        fields = {"city": "Munich", "country": None}
        # None value in lookup_sets simulates factAPI failure
        result, changes = svc.apply_to_entity_fields(
            fields, [fdef], {_MAPPING_KEY: None}
        )
        assert result["country"] is None
        assert changes == []

    @pytest.mark.asyncio
    async def test_fill_cache_hit_avoids_refetch(self, monkeypatch) -> None:
        svc = _svc()
        call_count = 0

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"city": "Munich", "country": "Germany"}]}

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return FakeResp()

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

        result1 = await svc.get_mapping("worldcities", "city", "country")
        result2 = await svc.get_mapping("worldcities", "city", "country")

        assert result1 == result2
        assert call_count == 1

    def test_fill_applied_in_v1_entity_list(self) -> None:
        svc = _svc()
        group = FieldGroup(
            name="company_locations",
            description="Locations",
            fields=[
                FieldDefinition(
                    name="city",
                    field_type="text",
                    description="City",
                    validators=[_fill_spec()],
                ),
                FieldDefinition(
                    name="country", field_type="text", description="Country"
                ),
            ],
            prompt_hint="",
            is_entity_list=True,
        )
        result_data = {
            "company_locations": [
                {"city": "Munich", "country": None},
                {"city": "San Jose", "country": None},  # ambiguous — skip
            ]
        }
        modified, changes = svc.apply_to_result(result_data, group, _fill_lookup_sets())
        items = modified["company_locations"]
        assert items[0]["country"] == "Germany"
        assert items[1]["country"] is None
        assert len(changes) == 1

    def test_fill_applied_in_v2_entity_list(self) -> None:
        svc = _svc()
        group = FieldGroup(
            name="company_locations",
            description="Locations",
            fields=[
                FieldDefinition(
                    name="city",
                    field_type="text",
                    description="City",
                    validators=[_fill_spec()],
                ),
                FieldDefinition(
                    name="country", field_type="text", description="Country"
                ),
            ],
            prompt_hint="",
            is_entity_list=True,
        )
        result_data = {
            "company_locations": {
                "items": [
                    {"fields": {"city": "Berlin", "country": None}, "confidence": 0.9},
                    {"fields": {"city": "Paris", "country": None}, "confidence": 0.8},
                ]
            }
        }
        modified, changes = svc.apply_to_result(result_data, group, _fill_lookup_sets())
        items = modified["company_locations"]["items"]
        assert items[0]["fields"]["country"] == "Germany"
        assert items[1]["fields"]["country"] == "France"
        assert len(changes) == 2

    def test_not_in_column_then_fill_runs_in_order(self) -> None:
        """city='Germany' → nullified by first validator, fill has no value → skip."""
        svc = _svc()
        fdef = _city_field_with_both_validators()
        country_fdef = FieldDefinition(
            name="country", field_type="text", description="Country"
        )
        fields = {"city": "Germany", "country": None}
        result, changes = svc.apply_to_entity_fields(
            fields, [fdef, country_fdef], _combined_lookup_sets()
        )
        assert result["city"] is None  # nullified
        assert result["country"] is None  # fill skipped because city was nullified
        assert len(changes) == 1  # only the nullify violation
