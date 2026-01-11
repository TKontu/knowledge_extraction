"""Tests for Prometheus metrics formatter."""

import pytest

from src.services.metrics.collector import SystemMetrics
from src.services.metrics.prometheus import format_prometheus


class TestPrometheusFormatter:
    """Tests for format_prometheus function."""

    def test_format_prometheus_includes_help(self) -> None:
        """Test that output includes HELP annotations."""
        metrics = SystemMetrics(
            jobs_total=10,
            jobs_by_type={"scrape": 5, "extract": 5},
            jobs_by_status={"completed": 8, "queued": 2},
            sources_total=20,
            sources_by_status={"completed": 15, "pending": 5},
            extractions_total=30,
            entities_total=100,
        )

        output = format_prometheus(metrics)

        assert "# HELP scristill_jobs_total" in output
        assert "# HELP scristill_jobs_by_type" in output
        assert "# HELP scristill_sources_total" in output

    def test_format_prometheus_includes_type(self) -> None:
        """Test that output includes TYPE annotations."""
        metrics = SystemMetrics(
            jobs_total=10,
            jobs_by_type={"scrape": 5, "extract": 5},
            jobs_by_status={"completed": 8, "queued": 2},
            sources_total=20,
            sources_by_status={"completed": 15, "pending": 5},
            extractions_total=30,
            entities_total=100,
        )

        output = format_prometheus(metrics)

        assert "# TYPE scristill_jobs_total gauge" in output
        assert "# TYPE scristill_jobs_by_type gauge" in output
        assert "# TYPE scristill_sources_total gauge" in output

    def test_format_prometheus_formats_labels(self) -> None:
        """Test that labels are formatted correctly."""
        metrics = SystemMetrics(
            jobs_total=10,
            jobs_by_type={"scrape": 5, "extract": 5},
            jobs_by_status={"completed": 8, "queued": 2},
            sources_total=20,
            sources_by_status={"completed": 15, "pending": 5},
            extractions_total=30,
            entities_total=100,
        )

        output = format_prometheus(metrics)

        # Check label formatting
        assert 'scristill_jobs_by_type{type="scrape"} 5' in output
        assert 'scristill_jobs_by_type{type="extract"} 5' in output
        assert 'scristill_jobs_by_status{status="completed"} 8' in output
        assert 'scristill_jobs_by_status{status="queued"} 2' in output

    def test_format_prometheus_valid_format(self) -> None:
        """Test that output is in valid Prometheus format."""
        metrics = SystemMetrics(
            jobs_total=10,
            jobs_by_type={"scrape": 5},
            jobs_by_status={"completed": 10},
            sources_total=20,
            sources_by_status={"completed": 20},
            extractions_total=30,
            entities_total=100,
        )

        output = format_prometheus(metrics)

        # Should end with newline
        assert output.endswith("\n")

        # Should have all required metrics
        assert "scristill_jobs_total 10" in output
        assert "scristill_sources_total 20" in output
        assert "scristill_extractions_total 30" in output
        assert "scristill_entities_total 100" in output

    def test_format_prometheus_with_empty_groups(self) -> None:
        """Test handling of empty metric groups."""
        metrics = SystemMetrics(
            jobs_total=0,
            jobs_by_type={},
            jobs_by_status={},
            sources_total=0,
            sources_by_status={},
            extractions_total=0,
            entities_total=0,
        )

        output = format_prometheus(metrics)

        # Should still have HELP and TYPE lines
        assert "# HELP scristill_jobs_total" in output
        assert "# TYPE scristill_jobs_total gauge" in output
        assert "scristill_jobs_total 0" in output
