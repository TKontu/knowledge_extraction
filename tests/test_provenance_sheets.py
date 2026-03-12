"""Tests for build_provenance_sheets() in unified 3-sheet mode."""

import pytest

from services.reports.consolidated_builder import SheetData, build_provenance_sheets


class _FakeRecord:
    """Minimal stand-in for ConsolidatedExtraction ORM object."""

    def __init__(self, provenance: dict | None = None):
        self.provenance = provenance


class TestBuildProvenanceSheets:
    def _make_data_sheet(self, rows, columns=None, name="Company Data"):
        columns = columns or ["source_group", "company_name", "employee_count"]
        labels = {c: c.replace("_", " ").title() for c in columns}
        return SheetData(
            name=name,
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

        assert quality.name == "Company Data - Quality"
        assert sources.name == "Company Data - Sources"

    def test_source_group_preserved(self):
        """source_group column is preserved in companion sheets."""
        data_sheet = self._make_data_sheet([
            {"source_group": "sg1", "company_name": "ABB", "employee_count": 100},
        ])
        records_by_sg = {"sg1": {"company_info": _FakeRecord(provenance={})}}

        quality, sources = build_provenance_sheets(data_sheet, records_by_sg, {})

        assert quality.rows[0]["source_group"] == "sg1"
        assert sources.rows[0]["source_group"] == "sg1"

    def test_list_suffix_stripping(self):
        """Entity list columns (e.g., products_gearbox_list) resolve provenance via _list stripping."""
        data_sheet = SheetData(
            name="Company Data",
            rows=[
                {"source_group": "sg1", "company_name": "ABB", "products_gearbox_list": "GearX (500Nm)"},
            ],
            columns=["source_group", "company_name", "products_gearbox_list"],
            labels={"source_group": "Source", "company_name": "Name", "products_gearbox_list": "Products"},
        )
        records_by_sg = {
            "sg1": {
                "company_info": _FakeRecord(provenance={
                    "company_name": {"winning_weight": 0.9, "top_sources": ["s1"]},
                }),
                "products_gearbox": _FakeRecord(provenance={
                    "products_gearbox": {"winning_weight": 0.85, "top_sources": ["s1", "s2"]},
                }),
            },
        }
        source_url_map = {
            "s1": "https://example.com/a",
            "s2": "https://example.com/b",
        }

        quality, sources = build_provenance_sheets(data_sheet, records_by_sg, source_url_map)

        # products_gearbox_list → strip _list → products_gearbox → found in provenance
        assert quality.rows[0]["products_gearbox_list"] == pytest.approx(0.85)
        assert "https://example.com/a" in sources.rows[0]["products_gearbox_list"]

    def test_entity_provenance_averaged(self):
        """Entity list quality shows average of per-entity winning_weights."""
        data_sheet = SheetData(
            name="Company Data",
            rows=[{"source_group": "sg1", "company_name": "ABB", "products_gearbox_list": "..."}],
            columns=["source_group", "company_name", "products_gearbox_list"],
            labels={"source_group": "Source", "company_name": "Name", "products_gearbox_list": "Products"},
        )
        records_by_sg = {
            "sg1": {
                "company_info": _FakeRecord(provenance={
                    "company_name": {"winning_weight": 0.9, "top_sources": ["s1"]},
                }),
                "products_gearbox": _FakeRecord(provenance={
                    "products_gearbox": {
                        "winning_weight": 0.85,
                        "top_sources": ["s1"],
                        "entity_provenance": [
                            {"winning_weight": 0.9, "top_sources": ["s1"]},
                            {"winning_weight": 0.7, "top_sources": ["s1"]},
                            {"winning_weight": 0.5, "top_sources": ["s1"]},
                        ],
                    },
                }),
            },
        }

        quality, _ = build_provenance_sheets(data_sheet, records_by_sg, {})

        # Scalar field uses winning_weight directly
        assert quality.rows[0]["company_name"] == pytest.approx(0.9)
        # Entity list uses average of per-entity weights that pass min_quality (0.3)
        # All 3 pass: (0.9 + 0.7 + 0.5) / 3 = 0.7
        assert quality.rows[0]["products_gearbox_list"] == pytest.approx(0.7, abs=0.001)

    def test_entity_provenance_filters_low_quality(self):
        """Entity provenance average excludes entities below quality threshold."""
        data_sheet = SheetData(
            name="Company Data",
            rows=[{"source_group": "sg1", "products_list": "..."}],
            columns=["source_group", "products_list"],
            labels={"source_group": "Source", "products_list": "Products"},
        )
        records_by_sg = {
            "sg1": {
                "products": _FakeRecord(provenance={
                    "products": {
                        "winning_weight": 0.5,
                        "top_sources": ["s1"],
                        "entity_provenance": [
                            {"winning_weight": 0.8, "top_sources": ["s1"]},
                            {"winning_weight": 0.1, "top_sources": ["s1"]},  # Below 0.3
                        ],
                    },
                }),
            },
        }

        quality, _ = build_provenance_sheets(data_sheet, records_by_sg, {})

        # Only entity with 0.8 passes, so average = 0.8
        assert quality.rows[0]["products_list"] == pytest.approx(0.8)

    def test_parametric_sheet_name(self):
        """Sheet name is derived from data_sheet.name, not hardcoded."""
        data_sheet = self._make_data_sheet(
            [{"source_group": "sg1", "company_name": "ABB", "employee_count": 100}],
            name="Job Listing Data",
        )
        records_by_sg = {"sg1": {"info": _FakeRecord(provenance={})}}

        quality, sources = build_provenance_sheets(data_sheet, records_by_sg, {})

        assert quality.name == "Job Listing Data - Quality"
        assert sources.name == "Job Listing Data - Sources"
