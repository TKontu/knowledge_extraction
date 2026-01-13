"""Tests for schema table report generation."""

from unittest.mock import MagicMock

import pytest

from services.reports.schema_table import SchemaTableReport


class TestSchemaTableReport:
    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    async def test_aggregate_booleans_any_true(self, mock_db):
        """Test boolean aggregation uses OR logic."""
        report = SchemaTableReport(mock_db)

        # Simulate multiple extractions
        data_list = [
            {"manufactures_gearboxes": False},
            {"manufactures_gearboxes": True},
            {"manufactures_gearboxes": False},
        ]

        from services.extraction.field_groups import MANUFACTURING_GROUP

        merged = report._merge_field_group_data(data_list, MANUFACTURING_GROUP)

        assert merged["manufactures_gearboxes"] is True

    async def test_format_product_list(self, mock_db):
        """Test product list formatting."""
        report = SchemaTableReport(mock_db)

        products = [
            {"product_name": "D Series", "power_rating_kw": 100, "ratio": "1:50"},
            {"product_name": "S Series", "torque_rating_nm": 5000},
        ]

        result = report._format_product_list(products)

        assert "D Series (100kW, 1:50)" in result
        assert "S Series (5000Nm)" in result
