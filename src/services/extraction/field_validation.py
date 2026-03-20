"""Declarative field validation backed by factAPI collections."""

import asyncio
from typing import Any

import httpx
import structlog

from services.extraction.field_groups import FieldDefinition, FieldGroup

logger = structlog.get_logger(__name__)

# Track which (collection, column) failures have been logged to avoid log spam
_logged_unavailable: set[str] = set()


class FieldValidationService:
    """Validates extracted field values against factAPI reference collections.

    Validators are declared on FieldDefinition objects and resolved against
    factAPI lookup sets fetched once per extraction run.
    """

    def __init__(
        self,
        factapi_url: str,
        api_key: str,
        timeout: float = 5.0,
        cache_ttl: int = 0,
    ) -> None:
        self._factapi_url = factapi_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        # Cache: key → frozenset | None (None = fetch failed)
        self._cache: dict[str, frozenset[str] | None] = {}
        self._in_flight: dict[str, asyncio.Lock] = {}
        # Mapping cache: key → {match_value: frozenset[fill_values]} | None
        self._mapping_cache: dict[str, dict[str, frozenset[str]] | None] = {}
        self._mapping_in_flight: dict[str, asyncio.Lock] = {}

    def _cache_key(self, collection: str, column: str, case_sensitive: bool) -> str:
        return f"{collection}/{column}/{'cs' if case_sensitive else 'ci'}"

    def _mapping_cache_key(
        self,
        collection: str,
        match_column: str,
        fill_column: str,
        case_sensitive: bool,
    ) -> str:
        return f"{collection}/{match_column}\u2192{fill_column}/{'cs' if case_sensitive else 'ci'}"

    async def get_lookup_set(
        self, collection: str, column: str, case_sensitive: bool = False
    ) -> frozenset[str] | None:
        """Fetch and cache a lookup set from factAPI.

        Args:
            collection: factAPI collection name (e.g. "worldcities").
            column: Column to retrieve (e.g. "country").
            case_sensitive: If False, values are lowercased.

        Returns:
            frozenset of values, or None if factAPI is unreachable.
        """
        key = self._cache_key(collection, column, case_sensitive)

        if key in self._cache:
            return self._cache[key]

        # Double-checked locking to prevent stampede
        if key not in self._in_flight:
            self._in_flight[key] = asyncio.Lock()

        async with self._in_flight[key]:
            # Re-check after acquiring lock
            if key in self._cache:
                return self._cache[key]

            result = await self._fetch_lookup_set(collection, column, case_sensitive)
            self._cache[key] = result
            return result

    async def _fetch_lookup_set(
        self, collection: str, column: str, case_sensitive: bool
    ) -> frozenset[str] | None:
        url = f"{self._factapi_url}/api/v1/collections/{collection}"
        params = {"_fields": column, "_limit": 100000}
        headers = {"X-API-Key": self._api_key} if self._api_key else {}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # factAPI returns {"data": [{column: value, ...}, ...]} or a list directly
            rows = data if isinstance(data, list) else data.get("data", [])
            values: set[str] = set()
            for row in rows:
                val = row.get(column)
                if val is not None:
                    s = str(val)
                    values.add(s if case_sensitive else s.lower())

            logger.info(
                "field_validation_lookup_fetched",
                collection=collection,
                column=column,
                count=len(values),
            )
            return frozenset(values)

        except Exception as e:
            log_key = f"{collection}/{column}"
            if log_key not in _logged_unavailable:
                _logged_unavailable.add(log_key)
                logger.warning(
                    "field_validation_factapi_unavailable",
                    collection=collection,
                    column=column,
                    error=str(e),
                )
            return None

    async def get_mapping(
        self,
        collection: str,
        match_column: str,
        fill_column: str,
        case_sensitive: bool = False,
    ) -> dict[str, frozenset[str]] | None:
        """Fetch and cache a value→fill mapping from factAPI.

        Args:
            collection: factAPI collection name (e.g. "worldcities").
            match_column: Column whose value is looked up (e.g. "city").
            fill_column: Column whose value is used to fill (e.g. "country").
            case_sensitive: If False, match keys are lowercased.

        Returns:
            Dict mapping match_value → frozenset of fill_values,
            or None if factAPI is unreachable.
        """
        key = self._mapping_cache_key(
            collection, match_column, fill_column, case_sensitive
        )

        if key in self._mapping_cache:
            return self._mapping_cache[key]

        if key not in self._mapping_in_flight:
            self._mapping_in_flight[key] = asyncio.Lock()

        async with self._mapping_in_flight[key]:
            if key in self._mapping_cache:
                return self._mapping_cache[key]

            result = await self._fetch_mapping(
                collection, match_column, fill_column, case_sensitive
            )
            self._mapping_cache[key] = result
            return result

    async def _fetch_mapping(
        self,
        collection: str,
        match_column: str,
        fill_column: str,
        case_sensitive: bool,
    ) -> dict[str, frozenset[str]] | None:
        url = f"{self._factapi_url}/api/v1/collections/{collection}"
        params = {"_fields": f"{match_column},{fill_column}", "_limit": 100000}
        headers = {"X-API-Key": self._api_key} if self._api_key else {}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            rows = data if isinstance(data, list) else data.get("data", [])
            mapping: dict[str, set[str]] = {}
            for row in rows:
                match_val = row.get(match_column)
                fill_val = row.get(fill_column)
                if match_val is None or fill_val is None:
                    continue
                mk = str(match_val) if case_sensitive else str(match_val).lower()
                fv = str(fill_val)
                mapping.setdefault(mk, set()).add(fv)

            result = {k: frozenset(v) for k, v in mapping.items()}
            logger.info(
                "field_validation_mapping_fetched",
                collection=collection,
                match_column=match_column,
                fill_column=fill_column,
                count=len(result),
            )
            return result

        except Exception as e:
            log_key = f"{collection}/{match_column}\u2192{fill_column}"
            if log_key not in _logged_unavailable:
                _logged_unavailable.add(log_key)
                logger.warning(
                    "field_validation_factapi_unavailable",
                    collection=collection,
                    column=f"{match_column}\u2192{fill_column}",
                    error=str(e),
                )
            return None

    async def prefetch_for_groups(
        self, field_groups: list[FieldGroup]
    ) -> dict[str, Any]:
        """Pre-fetch all lookup sets and mappings referenced by validators in the given groups.

        Args:
            field_groups: List of FieldGroup objects to scan for validators.

        Returns:
            Dict keyed by cache key → frozenset | mapping dict | None.
        """
        combos: set[tuple[str, str, bool]] = set()
        fill_combos: set[tuple[str, str, str, bool]] = set()

        for group in field_groups:
            for fdef in group.fields:
                if not fdef.validators:
                    continue
                for v in fdef.validators:
                    if (
                        v.type == "factapi_fill_from_lookup"
                        and v.fill_column is not None
                    ):
                        fill_combos.add(
                            (v.collection, v.column, v.fill_column, v.case_sensitive)
                        )
                    else:
                        combos.add((v.collection, v.column, v.case_sensitive))

        if not combos and not fill_combos:
            return {}

        combo_list = list(combos)
        fill_combo_list = list(fill_combos)
        tasks = [self.get_lookup_set(c, col, cs) for c, col, cs in combo_list]
        tasks += [self.get_mapping(c, mc, fc, cs) for c, mc, fc, cs in fill_combo_list]

        all_results = await asyncio.gather(*tasks)

        result: dict[str, Any] = {}
        for i, (c, col, cs) in enumerate(combo_list):
            result[self._cache_key(c, col, cs)] = all_results[i]
        for i, (c, mc, fc, cs) in enumerate(fill_combo_list):
            result[self._mapping_cache_key(c, mc, fc, cs)] = all_results[
                len(combo_list) + i
            ]
        return result

    def apply_to_entity_fields(
        self,
        fields: dict[str, Any],
        field_defs: list[FieldDefinition],
        lookup_sets: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict]]:
        """Apply validators to a flat field dict for one entity.

        Args:
            fields: Flat field dict (e.g. {"city": "Germany", "country": None}).
            field_defs: FieldDefinition list for this group.
            lookup_sets: Pre-fetched lookup sets and mappings keyed by cache key.

        Returns:
            Tuple of (modified_fields, changes).
            changes is a list of dicts describing each validator action taken.
        """
        modified = dict(fields)
        changes: list[dict] = []

        for fdef in field_defs:
            if not fdef.validators:
                continue
            value = modified.get(fdef.name)
            if value is None or value == "":
                continue

            for spec in fdef.validators:
                if spec.type == "factapi_fill_from_lookup":
                    # Re-read current value — may have been nullified by a prior validator
                    current_value = modified.get(fdef.name)
                    if current_value is None or current_value == "":
                        continue
                    mapping_key = self._mapping_cache_key(
                        spec.collection,
                        spec.column,
                        spec.fill_column,
                        spec.case_sensitive,
                    )
                    mapping = lookup_sets.get(mapping_key)
                    if mapping is None:
                        continue
                    check_value = (
                        str(current_value)
                        if spec.case_sensitive
                        else str(current_value).lower()
                    )
                    mapped_values = mapping.get(check_value)
                    if not mapped_values:
                        continue
                    if spec.unique_only and len(mapped_values) != 1:
                        continue
                    fill_value = next(iter(mapped_values))
                    target_current = modified.get(spec.target_field)
                    if spec.action == "fill_if_null" and target_current not in (
                        None,
                        "",
                    ):
                        continue
                    modified[spec.target_field] = fill_value
                    changes.append(
                        {
                            "field": fdef.name,
                            "target_field": spec.target_field,
                            "filled_value": fill_value,
                            "action": spec.action,
                        }
                    )
                    continue

                # Check validators: factapi_not_in_column, factapi_exists_in_column
                key = self._cache_key(spec.collection, spec.column, spec.case_sensitive)
                lookup_set = lookup_sets.get(key)

                if lookup_set is None:
                    # factAPI unavailable — skip silently (already logged)
                    continue

                check_value = str(value) if spec.case_sensitive else str(value).lower()

                if spec.type == "factapi_not_in_column":
                    violated = check_value in lookup_set
                elif spec.type == "factapi_exists_in_column":
                    violated = check_value not in lookup_set
                else:
                    continue

                if violated:
                    changes.append(
                        {
                            "field": fdef.name,
                            "value": value,
                            "validator_type": spec.type,
                            "action": spec.action,
                        }
                    )
                    if spec.action == "nullify":
                        modified[fdef.name] = None

        return modified, changes

    def apply_to_result(
        self,
        result_data: dict,
        group: FieldGroup,
        lookup_sets: dict[str, frozenset | None],
    ) -> tuple[dict, list[dict]]:
        """Apply validators to an extraction result dict for the given group.

        Supports both v1 (flat entity list) and v2 (items with fields) formats.

        Args:
            result_data: The extraction result data dict.
            group: FieldGroup with validator specs on fields.
            lookup_sets: Pre-fetched lookup sets.

        Returns:
            Tuple of (modified_result_data, all_violations).
        """
        all_violations: list[dict] = []
        modified = dict(result_data)

        group_data = modified.get(group.name)

        if group.is_entity_list and isinstance(group_data, list):
            # v1 entity list: [{field: value, ...}, ...]
            new_items = []
            for item in group_data:
                if not isinstance(item, dict):
                    new_items.append(item)
                    continue
                fixed, violations = self.apply_to_entity_fields(
                    item, group.fields, lookup_sets
                )
                new_items.append(fixed)
                all_violations.extend(violations)
            modified[group.name] = new_items

        elif group.is_entity_list and isinstance(group_data, dict):
            # v2 entity list: {"items": [{"fields": {...}, ...}]}
            items = group_data.get("items", [])
            new_items = []
            for item in items:
                if not isinstance(item, dict):
                    new_items.append(item)
                    continue
                item_fields = item.get("fields", {})
                if isinstance(item_fields, dict):
                    fixed_fields, violations = self.apply_to_entity_fields(
                        item_fields, group.fields, lookup_sets
                    )
                    new_item = dict(item)
                    new_item["fields"] = fixed_fields
                    new_items.append(new_item)
                    all_violations.extend(violations)
                else:
                    new_items.append(item)
            modified[group.name] = dict(group_data)
            modified[group.name]["items"] = new_items

        elif isinstance(group_data, dict):
            # v1 flat (non-entity-list): validate top-level field values
            fixed, violations = self.apply_to_entity_fields(
                group_data, group.fields, lookup_sets
            )
            modified[group.name] = fixed
            all_violations.extend(violations)

        return modified, all_violations
