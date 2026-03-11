"""Tests for consolidated table report generation."""

from unittest.mock import MagicMock

import pytest

from services.reports.consolidated_builder import (
    ConsolidatedReportBuilder,
    ConsolidatedReportData,
    SheetData,
    compose_multi_sheet,
    compose_single_sheet,
    render_markdown,
    validate_entity_focus,
)
from services.reports.excel_formatter import ExcelFormatter
from services.reports.schema_table_generator import SchemaTableGenerator


# ── Fixtures ──


@pytest.fixture
def sample_schema():
    """Schema with 2 scalar groups and 1 entity list."""
    return {
        "name": "drivetrain_schema",
        "field_groups": [
            {
                "name": "company_meta",
                "description": "Company metadata",
                "prompt_hint": "Extract company info",
                "fields": [
                    {"name": "company_name", "field_type": "text", "description": "Name"},
                    {"name": "founded_year", "field_type": "integer", "description": "Founded year"},
                    {"name": "is_oem", "field_type": "boolean", "description": "Is OEM"},
                ],
            },
            {
                "name": "certifications",
                "description": "Company certifications",
                "prompt_hint": "Extract certs",
                "fields": [
                    {"name": "iso_certified", "field_type": "boolean", "description": "ISO certified"},
                    {"name": "cert_list", "field_type": "list", "description": "Certifications"},
                ],
            },
            {
                "name": "products_gearbox",
                "description": "Gearbox products",
                "prompt_hint": "Extract products",
                "is_entity_list": True,
                "fields": [
                    {"name": "product_name", "field_type": "text", "description": "Product"},
                    {"name": "max_torque_nm", "field_type": "float", "description": "Max torque"},
                    {"name": "gear_type", "field_type": "enum", "description": "Gear type",
                     "enum_values": ["spur", "helical", "planetary"]},
                ],
            },
        ],
    }


@pytest.fixture
def collision_schema():
    """Schema with field name collisions across groups."""
    return {
        "name": "collision_schema",
        "field_groups": [
            {
                "name": "group_a",
                "description": "Group A",
                "prompt_hint": "...",
                "fields": [
                    {"name": "name", "field_type": "text", "description": "Name A"},
                    {"name": "unique_a", "field_type": "text", "description": "Unique A"},
                ],
            },
            {
                "name": "group_b",
                "description": "Group B",
                "prompt_hint": "...",
                "fields": [
                    {"name": "name", "field_type": "text", "description": "Name B"},
                    {"name": "unique_b", "field_type": "text", "description": "Unique B"},
                ],
            },
        ],
    }


@pytest.fixture
def generator():
    return SchemaTableGenerator()


def _make_record(source_group, extraction_type, data, provenance=None, source_count=3):
    """Create a mock ConsolidatedExtraction record."""
    rec = MagicMock()
    rec.source_group = source_group
    rec.extraction_type = extraction_type
    rec.data = data
    rec.provenance = provenance or {}
    rec.source_count = source_count
    rec.grounded_count = 2
    return rec


@pytest.fixture
def sample_records():
    """Two companies with scalar + entity data."""
    return [
        # Company A - scalar
        _make_record("Acme Corp", "company_meta", {
            "company_name": "Acme Corporation",
            "founded_year": 1995,
            "is_oem": True,
        }, provenance={
            "company_name": {"agreement": 0.9, "grounded_count": 3, "strategy": "frequency"},
            "founded_year": {"agreement": 0.8, "grounded_count": 2, "strategy": "weighted_median"},
            "is_oem": {"agreement": 1.0, "grounded_count": 1, "strategy": "any_true"},
        }),
        _make_record("Acme Corp", "certifications", {
            "iso_certified": True,
            "cert_list": ["ISO 9001", "ISO 14001"],
        }),
        # Company A - entity
        _make_record("Acme Corp", "products_gearbox", {
            "products_gearbox": [
                {"product_name": "GearX-100", "max_torque_nm": 500.0, "gear_type": "helical"},
                {"product_name": "GearX-200", "max_torque_nm": 1000.0, "gear_type": "planetary"},
            ]
        }),
        # Company B - scalar
        _make_record("Beta Inc", "company_meta", {
            "company_name": "Beta Industries",
            "founded_year": 2003,
            "is_oem": False,
        }),
        _make_record("Beta Inc", "certifications", {
            "iso_certified": False,
            "cert_list": ["CE"],
        }),
        # Company B - entity
        _make_record("Beta Inc", "products_gearbox", {
            "products_gearbox": [
                {"product_name": "BetaDrive", "max_torque_nm": 250.0, "gear_type": "spur"},
            ]
        }),
    ]


# ── TestGetScalarColumns ──


class TestGetScalarColumns:
    def test_excludes_entity_list(self, generator, sample_schema):
        columns, labels, col_types = generator.get_scalar_columns(sample_schema)
        assert "products_gearbox" not in columns
        assert "product_name" not in columns

    def test_includes_source_group(self, generator, sample_schema):
        columns, labels, _ = generator.get_scalar_columns(sample_schema)
        assert columns[0] == "source_group"
        assert labels["source_group"] == "Source"

    def test_no_source_url_columns(self, generator, sample_schema):
        columns, _, _ = generator.get_scalar_columns(sample_schema)
        assert "source_url" not in columns
        assert "source_title" not in columns
        assert "domain" not in columns
        assert "avg_confidence" not in columns

    def test_collision_prefixing(self, generator, collision_schema):
        columns, labels, _ = generator.get_scalar_columns(collision_schema)
        assert "group_a.name" in columns
        assert "group_b.name" in columns
        assert "unique_a" in columns
        assert "unique_b" in columns


# ── TestGetEntityGroupColumns ──


class TestGetEntityGroupColumns:
    def test_all_fields_for_group(self, generator, sample_schema):
        columns, labels, col_types = generator.get_entity_group_columns(
            sample_schema, "products_gearbox"
        )
        assert "source_group" in columns
        assert "product_name" in columns
        assert "max_torque_nm" in columns
        assert "gear_type" in columns

    def test_source_group_prepended(self, generator, sample_schema):
        columns, _, _ = generator.get_entity_group_columns(
            sample_schema, "products_gearbox"
        )
        assert columns[0] == "source_group"

    def test_unit_inference_in_labels(self, generator, sample_schema):
        _, labels, _ = generator.get_entity_group_columns(
            sample_schema, "products_gearbox"
        )
        assert "Nm" in labels["max_torque_nm"]

    def test_invalid_group_raises(self, generator, sample_schema):
        with pytest.raises(ValueError, match="not found"):
            generator.get_entity_group_columns(sample_schema, "nonexistent")


# ── TestBuildCompanyRow ──


class TestBuildCompanyRow:
    def test_merges_scalar_groups(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        acme_row = data.company_sheet.rows[0]
        assert acme_row["source_group"] == "Acme Corp"
        assert acme_row["company_name"] == "Acme Corporation"
        assert acme_row["founded_year"] == 1995
        assert acme_row["is_oem"] is True

    def test_flat_list_inline(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        acme_row = data.company_sheet.rows[0]
        assert acme_row["cert_list"] == "ISO 9001, ISO 14001"

    def test_missing_extraction_type_none(self, generator, sample_schema):
        records = [
            _make_record("Lonely Corp", "company_meta", {
                "company_name": "Lonely",
                "founded_year": 2020,
                "is_oem": False,
            }),
            # No certifications record
        ]
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(records, sample_schema)
        row = data.company_sheet.rows[0]
        assert row.get("iso_certified") is None

    def test_provenance_columns(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema, include_provenance=True)
        acme_row = data.company_sheet.rows[0]
        assert "source_count" in acme_row
        assert "avg_agreement" in acme_row
        assert "grounded_pct" in acme_row
        assert acme_row["source_count"] == 3

    def test_provenance_columns_in_sheet(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema, include_provenance=True)
        assert "source_count" in data.company_sheet.columns
        assert "avg_agreement" in data.company_sheet.columns
        assert "grounded_pct" in data.company_sheet.columns


# ── TestBuildEntityRows ──


class TestBuildEntityRows:
    def test_one_row_per_entity(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        products_sheet = data.entity_sheets[0]
        # Acme has 2 products, Beta has 1
        assert len(products_sheet.rows) == 3

    def test_source_group_prepended(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        products_sheet = data.entity_sheets[0]
        for row in products_sheet.rows:
            assert "source_group" in row

    def test_empty_entity_list(self, generator, sample_schema):
        records = [
            _make_record("Empty Corp", "company_meta", {"company_name": "Empty"}),
            _make_record("Empty Corp", "products_gearbox", {"products_gearbox": []}),
        ]
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(records, sample_schema)
        products_sheet = data.entity_sheets[0]
        assert len(products_sheet.rows) == 0

    def test_all_fields_present(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        products_sheet = data.entity_sheets[0]
        first_row = products_sheet.rows[0]
        assert first_row["product_name"] == "GearX-100"
        assert first_row["max_torque_nm"] == 500.0
        assert first_row["gear_type"] == "helical"


# ── TestComposeMultiSheet ──


class TestComposeMultiSheet:
    def test_correct_sheet_count(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheets = compose_multi_sheet(data)
        # company + 1 entity sheet with data
        assert len(sheets) >= 2
        assert sheets[0].name == "Companies"

    def test_skip_empty_entities(self, generator, sample_schema):
        records = [
            _make_record("Corp", "company_meta", {"company_name": "Corp"}),
            _make_record("Corp", "products_gearbox", {"products_gearbox": []}),
        ]
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(records, sample_schema)
        sheets = compose_multi_sheet(data)
        # Only company sheet, entity sheet has no rows
        assert len(sheets) == 1

    def test_ordering(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheets = compose_multi_sheet(data)
        assert sheets[0].name == "Companies"
        # Entity sheets follow
        for s in sheets[1:]:
            assert s.name != "Companies"


# ── TestComposeSingleSheet ──


class TestComposeSingleSheet:
    def test_company_only_mode(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheet = compose_single_sheet(data, None, sample_schema)
        # Should have entity count columns
        count_cols = [c for c in sheet.columns if c.endswith("_count")]
        assert len(count_cols) > 0
        # Row count = number of companies
        assert len(sheet.rows) == 2

    def test_entity_focused_denormalized_raw_name(self, generator, sample_schema, sample_records):
        """Raw schema name (products_gearbox) works for entity_focus."""
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheet = compose_single_sheet(data, "products_gearbox", sample_schema)
        # Denormalized: 2 products for Acme + 1 for Beta = 3 rows
        assert len(sheet.rows) == 3
        assert sheet.rows[0]["company_name"] == "Acme Corporation"
        # Second row for same company has blank company cols (except source_group)
        assert sheet.rows[1].get("company_name") is None

    def test_entity_focused_denormalized_humanized_name(self, generator, sample_schema, sample_records):
        """Humanized display name (Products Gearbox) also works for entity_focus."""
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheet = compose_single_sheet(data, "Products Gearbox", sample_schema)
        assert len(sheet.rows) == 3
        assert sheet.rows[0]["company_name"] == "Acme Corporation"

    def test_all_entities_superset(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheet = compose_single_sheet(data, "all", sample_schema)
        assert "entity_type" in sheet.columns
        # All entity rows
        assert len(sheet.rows) == 3

    def test_invalid_entity_focus_raises(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        with pytest.raises(ValueError, match="Unknown entity group"):
            compose_single_sheet(data, "nonexistent_group", sample_schema)


# ── TestMultiSheetExcel ──


class TestMultiSheetExcel:
    def test_sheet_count(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheets = compose_multi_sheet(data)
        formatter = ExcelFormatter()
        excel_bytes = formatter.create_multi_sheet_workbook(sheets)
        assert isinstance(excel_bytes, bytes)
        assert len(excel_bytes) > 0

        # Verify sheet count by loading
        from openpyxl import load_workbook
        from io import BytesIO

        wb = load_workbook(BytesIO(excel_bytes))
        assert len(wb.sheetnames) == len(sheets)

    def test_sheet_names_sanitized(self):
        sheets = [
            SheetData(
                name="Invalid/Name:Here",
                rows=[{"a": 1}],
                columns=["a"],
                labels={"a": "A"},
            )
        ]
        formatter = ExcelFormatter()
        excel_bytes = formatter.create_multi_sheet_workbook(sheets)

        from openpyxl import load_workbook
        from io import BytesIO

        wb = load_workbook(BytesIO(excel_bytes))
        # Should not contain invalid chars
        assert "/" not in wb.sheetnames[0]
        assert ":" not in wb.sheetnames[0]

    def test_existing_create_workbook_unchanged(self):
        """Ensure single-sheet create_workbook still works."""
        formatter = ExcelFormatter()
        rows = [{"name": "Alice", "age": 30}]
        columns = ["name", "age"]
        labels = {"name": "Name", "age": "Age"}
        result = formatter.create_workbook(rows, columns, labels, "Test")
        assert isinstance(result, bytes)
        assert len(result) > 0


# ── TestRenderMarkdown ──


class TestRenderMarkdown:
    def test_section_headers(self, generator, sample_schema, sample_records):
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheets = compose_multi_sheet(data)
        md = render_markdown(sheets, data.summary)
        assert "## Companies" in md
        assert "## Products Gearbox" in md

    def test_no_hardcoded_title(self, generator, sample_schema, sample_records):
        """render_markdown should not inject its own H1 title."""
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        sheets = compose_multi_sheet(data)
        md = render_markdown(sheets, data.summary)
        # No H1 header — caller controls the title
        assert not md.startswith("# ")

    def test_pipe_escaping(self):
        sheets = [
            SheetData(
                name="Test",
                rows=[{"val": "has|pipe"}],
                columns=["val"],
                labels={"val": "Value"},
            )
        ]
        md = render_markdown(sheets, {"total_companies": 1})
        assert "has\\|pipe" in md
        assert "has|pipe" not in md.split("\n")[-2]  # Not raw in table row


# ── TestValidateEntityFocus ──


class TestEntitySheetKey:
    def test_entity_sheets_have_key(self, generator, sample_schema, sample_records):
        """Entity sheets store raw group name as key for reliable lookups."""
        builder = ConsolidatedReportBuilder(generator)
        data = builder.gather(sample_records, sample_schema)
        products_sheet = data.entity_sheets[0]
        assert products_sheet.key == "products_gearbox"
        assert products_sheet.name == "Products Gearbox"


class TestValidateEntityFocus:
    def test_valid_group(self, sample_schema):
        validate_entity_focus("products_gearbox", sample_schema)  # Should not raise

    def test_all_accepted(self, sample_schema):
        validate_entity_focus("all", sample_schema)  # Should not raise

    def test_invalid_raises(self, sample_schema):
        with pytest.raises(ValueError, match="Unknown entity group"):
            validate_entity_focus("nonexistent", sample_schema)


# ── TestReportRequestModel ──


class TestReportRequestModel:
    def test_consolidated_valid(self):
        from models import ReportRequest, ReportType

        req = ReportRequest(
            type=ReportType.TABLE,
            group_by="consolidated",
            layout="multi_sheet",
        )
        assert req.group_by == "consolidated"

    def test_entity_focus_requires_single_sheet(self):
        from models import ReportRequest, ReportType

        with pytest.raises(Exception):
            ReportRequest(
                type=ReportType.TABLE,
                group_by="consolidated",
                layout="multi_sheet",
                entity_focus="products",
            )

    def test_single_sheet_requires_consolidated(self):
        from models import ReportRequest, ReportType

        with pytest.raises(Exception):
            ReportRequest(
                type=ReportType.TABLE,
                group_by="source",
                layout="single_sheet",
            )

    def test_consolidated_requires_table(self):
        from models import ReportRequest, ReportType

        with pytest.raises(Exception):
            ReportRequest(
                type=ReportType.SINGLE,
                group_by="consolidated",
            )
