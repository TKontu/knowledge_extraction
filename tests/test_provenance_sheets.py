"""Tests for build_provenance_sheets() and provenance_sheets validation."""

import pytest

from models import ReportRequest
from services.reports.consolidated_builder import SheetData, build_provenance_sheets


class _FakeRecord:
    """Minimal stand-in for ConsolidatedExtraction ORM object."""

    def __init__(self, provenance: dict | None = None):
        self.provenance = provenance


class TestBuildProvenanceSheets:
    def _make_data_sheet(self, rows, columns=None):
        columns = columns or ["source_group", "company_name", "employee_count"]
        labels = {c: c.replace("_", " ").title() for c in columns}
        return SheetData(
            name="Companies",
            rows=rows,
            columns=columns,
            labels=labels,
        )

    def test_same_row_count(self):
        """Quality and Sources sheets have same number of rows as data sheet."""
        data_sheet = self._make_data_sheet([
            {"source_group": "sg1", "company_name": "ABB", "employee_count": 100},
            {"source_group": "sg2", "company_name": "Siemens", "employee_count": 200},
        ])
        records_by_sg = {
            "sg1": {
                "company_info": _FakeRecord(provenance={
                    "company_name": {
                        "winning_weight": 0.9,
                        "top_sources": ["s1", "s2"],
                    },
                    "employee_count": {
                        "winning_weight": 0.85,
                        "top_sources": ["s1"],
                    },
                }),
            },
            "sg2": {
                "company_info": _FakeRecord(provenance={
                    "company_name": {
                        "winning_weight": 0.7,
                        "top_sources": ["s3"],
                    },
                    "employee_count": {
                        "winning_weight": 0.6,
                        "top_sources": ["s3"],
                    },
                }),
            },
        }
        source_url_map = {
            "s1": "https://example.com/page1",
            "s2": "https://example.com/page2",
            "s3": "https://example.com/page3",
        }

        quality, sources = build_provenance_sheets(data_sheet, records_by_sg, source_url_map)

        assert len(quality.rows) == 2
        assert len(sources.rows) == 2

    def test_same_columns(self):
        """Quality and Sources sheets have same columns as data sheet."""
        data_sheet = self._make_data_sheet([
            {"source_group": "sg1", "company_name": "ABB", "employee_count": 100},
        ])
        records_by_sg = {"sg1": {"company_info": _FakeRecord(provenance={})}}

        quality, sources = build_provenance_sheets(data_sheet, records_by_sg, {})

        assert quality.columns == data_sheet.columns
        assert sources.columns == data_sheet.columns

    def test_quality_values(self):
        """Quality sheet contains winning_weight values."""
        data_sheet = self._make_data_sheet([
            {"source_group": "sg1", "company_name": "ABB", "employee_count": 100},
        ])
        records_by_sg = {
            "sg1": {
                "company_info": _FakeRecord(provenance={
                    "company_name": {"winning_weight": 0.92, "top_sources": []},
                    "employee_count": {"winning_weight": 0.85, "top_sources": []},
                }),
            },
        }

        quality, _ = build_provenance_sheets(data_sheet, records_by_sg, {})

        assert quality.rows[0]["company_name"] == pytest.approx(0.92)
        assert quality.rows[0]["employee_count"] == pytest.approx(0.85)

    def test_sources_resolved_to_urls(self):
        """Sources sheet resolves source_ids to URLs."""
        data_sheet = self._make_data_sheet([
            {"source_group": "sg1", "company_name": "ABB", "employee_count": 100},
        ])
        records_by_sg = {
            "sg1": {
                "company_info": _FakeRecord(provenance={
                    "company_name": {
                        "winning_weight": 0.9,
                        "top_sources": ["s1", "s2"],
                    },
                    "employee_count": {
                        "winning_weight": 0.8,
                        "top_sources": ["s1"],
                    },
                }),
            },
        }
        source_url_map = {
            "s1": "https://example.com/a",
            "s2": "https://example.com/b",
        }

        _, sources = build_provenance_sheets(data_sheet, records_by_sg, source_url_map)

        assert "https://example.com/a" in sources.rows[0]["company_name"]
        assert "https://example.com/b" in sources.rows[0]["company_name"]
        assert sources.rows[0]["employee_count"] == "https://example.com/a"

    def test_missing_winning_weight_is_na(self):
        """Missing winning_weight in provenance → 'N/A' in quality cell."""
        data_sheet = self._make_data_sheet([
            {"source_group": "sg1", "company_name": "ABB", "employee_count": 100},
        ])
        records_by_sg = {
            "sg1": {
                "company_info": _FakeRecord(provenance={
                    "company_name": {"top_sources": ["s1"]},
                    # employee_count not in provenance at all
                }),
            },
        }

        quality, _ = build_provenance_sheets(data_sheet, records_by_sg, {})

        assert quality.rows[0]["company_name"] == "N/A"  # no winning_weight key
        assert quality.rows[0]["employee_count"] == "N/A"  # field not in provenance

    def test_missing_source_group_in_records(self):
        """Source group not found in records → all N/A."""
        data_sheet = self._make_data_sheet([
            {"source_group": "unknown_sg", "company_name": "X", "employee_count": 1},
        ])

        quality, sources = build_provenance_sheets(data_sheet, {}, {})

        assert quality.rows[0]["company_name"] == "N/A"
        assert sources.rows[0]["company_name"] == "N/A"

    def test_sheet_names(self):
        """Companion sheets have correct names."""
        data_sheet = self._make_data_sheet([
            {"source_group": "sg1", "company_name": "ABB", "employee_count": 100},
        ])
        records_by_sg = {"sg1": {"company_info": _FakeRecord(provenance={})}}

        quality, sources = build_provenance_sheets(data_sheet, records_by_sg, {})

        assert quality.name == "Companies - Quality"
        assert sources.name == "Companies - Sources"

    def test_source_group_preserved(self):
        """source_group column is preserved in companion sheets."""
        data_sheet = self._make_data_sheet([
            {"source_group": "sg1", "company_name": "ABB", "employee_count": 100},
        ])
        records_by_sg = {"sg1": {"company_info": _FakeRecord(provenance={})}}

        quality, sources = build_provenance_sheets(data_sheet, records_by_sg, {})

        assert quality.rows[0]["source_group"] == "sg1"
        assert sources.rows[0]["source_group"] == "sg1"

    def test_entity_sheet_uses_list_level_provenance(self):
        """Entity sheets use list-level provenance keyed by entity group name."""
        # Entity sheet: key="products", columns are individual field names
        entity_sheet = SheetData(
            name="Products",
            rows=[
                {"source_group": "sg1", "name": "Motor X", "type": "AC"},
                {"source_group": "sg1", "name": "Drive Y", "type": "VFD"},
            ],
            columns=["source_group", "name", "type"],
            labels={"source_group": "Source", "name": "Name", "type": "Type"},
            key="products",  # This marks it as an entity sheet
        )
        records_by_sg = {
            "sg1": {
                "products": _FakeRecord(provenance={
                    # Provenance is keyed by entity group name, not field names
                    "products": {
                        "winning_weight": 0.82,
                        "top_sources": ["s1", "s2"],
                    },
                }),
            },
        }
        source_url_map = {
            "s1": "https://example.com/a",
            "s2": "https://example.com/b",
        }

        quality, sources = build_provenance_sheets(
            entity_sheet, records_by_sg, source_url_map
        )

        # All entity rows get the list-level provenance
        assert quality.rows[0]["name"] == pytest.approx(0.82)
        assert quality.rows[0]["type"] == pytest.approx(0.82)
        assert quality.rows[1]["name"] == pytest.approx(0.82)

        # Sources resolved for all entity columns
        assert "https://example.com/a" in sources.rows[0]["name"]
        assert "https://example.com/b" in sources.rows[0]["name"]

    def test_entity_sheet_no_key_stays_na(self):
        """Sheets without key don't use entity fallback."""
        data_sheet = SheetData(
            name="Companies",
            rows=[{"source_group": "sg1", "unknown_col": "X"}],
            columns=["source_group", "unknown_col"],
            labels={"source_group": "Source", "unknown_col": "Unknown"},
            key="",  # No entity key
        )
        records_by_sg = {
            "sg1": {
                "company_info": _FakeRecord(provenance={
                    "company_name": {"winning_weight": 0.9, "top_sources": []},
                }),
            },
        }

        quality, _ = build_provenance_sheets(data_sheet, records_by_sg, {})
        assert quality.rows[0]["unknown_col"] == "N/A"


class TestProvenanceSheetsValidation:
    def test_rejects_provenance_sheets_with_md_format(self):
        """provenance_sheets=True requires output_format='xlsx'."""
        with pytest.raises(ValueError, match="provenance_sheets requires output_format='xlsx'"):
            ReportRequest(
                type="table",
                group_by="consolidated",
                output_format="md",
                provenance_sheets=True,
            )

    def test_rejects_provenance_sheets_without_consolidated(self):
        """provenance_sheets=True requires group_by='consolidated'."""
        with pytest.raises(ValueError, match="provenance_sheets requires group_by='consolidated'"):
            ReportRequest(
                type="table",
                group_by="source",
                output_format="xlsx",
                provenance_sheets=True,
            )

    def test_accepts_valid_provenance_sheets_request(self):
        """Valid combination: xlsx + consolidated + provenance_sheets."""
        req = ReportRequest(
            type="table",
            group_by="consolidated",
            output_format="xlsx",
            provenance_sheets=True,
        )
        assert req.provenance_sheets is True
