"""Tests for ReportService."""

from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest

from models import ReportRequest, ReportType
from orm_models import Report
from services.llm.client import LLMClient
from services.reports.service import ReportData, ReportService
from services.storage.repositories.entity import EntityRepository
from services.storage.repositories.extraction import ExtractionRepository


@pytest.fixture
def mock_extraction_repo():
    """Mock ExtractionRepository."""
    return Mock(spec=ExtractionRepository)


@pytest.fixture
def mock_entity_repo():
    """Mock EntityRepository."""
    return Mock(spec=EntityRepository)


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient."""
    client = Mock(spec=LLMClient)
    client.client = Mock()
    client.model = "gpt-4"
    return client


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    session = MagicMock()
    session.add = Mock()
    session.commit = Mock()
    session.refresh = Mock()
    return session


@pytest.fixture
def report_service(
    mock_extraction_repo, mock_entity_repo, mock_llm_client, mock_db_session
):
    """Create ReportService instance with mocked dependencies."""
    return ReportService(
        extraction_repo=mock_extraction_repo,
        entity_repo=mock_entity_repo,
        llm_client=mock_llm_client,
        db_session=mock_db_session,
    )


class TestReportServiceInit:
    """Test ReportService initialization."""

    def test_init_with_dependencies(
        self, mock_extraction_repo, mock_entity_repo, mock_llm_client, mock_db_session
    ):
        """Test ReportService initializes with dependencies."""
        service = ReportService(
            extraction_repo=mock_extraction_repo,
            entity_repo=mock_entity_repo,
            llm_client=mock_llm_client,
            db_session=mock_db_session,
        )

        assert service._extraction_repo == mock_extraction_repo
        assert service._entity_repo == mock_entity_repo
        assert service._llm_client == mock_llm_client
        assert service._db == mock_db_session


class TestReportServiceGenerate:
    """Test ReportService.generate() method."""

    @pytest.mark.asyncio
    async def test_generate_returns_report(
        self, report_service, mock_extraction_repo, mock_entity_repo, mock_db_session
    ):
        """Test generate() returns a Report object."""
        # Setup
        project_id = uuid4()
        request = ReportRequest(
            type=ReportType.SINGLE,
            source_groups=["company-a"],
        )

        # Mock repository responses
        mock_extraction_repo.list = AsyncMock(return_value=[])
        mock_entity_repo.list = AsyncMock(return_value=[])

        # Execute
        report = await report_service.generate(project_id, request)

        # Verify
        assert isinstance(report, Report)
        assert report.type == "single"
        assert report.source_groups == ["company-a"]
        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()


class TestReportServiceGatherData:
    """Test ReportService._gather_data() method."""

    @pytest.mark.asyncio
    async def test_gather_data_queries_extractions(
        self, report_service, mock_extraction_repo, mock_entity_repo
    ):
        """Test _gather_data() queries ExtractionRepository."""
        # Setup
        project_id = uuid4()
        source_groups = ["company-a"]
        mock_extraction_repo.list = AsyncMock(return_value=[])
        mock_entity_repo.list = AsyncMock(return_value=[])

        # Execute
        await report_service._gather_data(
            project_id=project_id,
            source_groups=source_groups,
            categories=None,
            entity_types=None,
            max_extractions=50,
        )

        # Verify
        mock_extraction_repo.list.assert_called()

    @pytest.mark.asyncio
    async def test_gather_data_queries_entities(
        self, report_service, mock_extraction_repo, mock_entity_repo
    ):
        """Test _gather_data() queries EntityRepository when entity_types provided."""
        # Setup
        project_id = uuid4()
        source_groups = ["company-a"]
        entity_types = ["limit", "pricing"]
        mock_extraction_repo.list = AsyncMock(return_value=[])
        mock_entity_repo.list = AsyncMock(return_value=[])

        # Execute
        await report_service._gather_data(
            project_id=project_id,
            source_groups=source_groups,
            categories=None,
            entity_types=entity_types,
            max_extractions=50,
        )

        # Verify
        mock_entity_repo.list.assert_called()


class TestReportData:
    """Test ReportData dataclass."""

    def test_report_data_structure(self):
        """Test ReportData has correct structure."""
        data = ReportData(
            extractions_by_group={"company-a": []},
            entities_by_group={"company-a": {"limit": []}},
            source_groups=["company-a"],
            extraction_ids=[],
            entity_count=0,
        )

        assert data.extractions_by_group == {"company-a": []}
        assert data.entities_by_group == {"company-a": {"limit": []}}
        assert data.source_groups == ["company-a"]
        assert data.extraction_ids == []
        assert data.entity_count == 0


class TestGenerateSingleReport:
    """Test _generate_single_report() method."""

    @pytest.mark.asyncio
    async def test_generate_single_report_has_title(self, report_service):
        """Test single report has proper title."""
        data = ReportData(
            extractions_by_group={"company-a": []},
            entities_by_group={},
            source_groups=["company-a"],
            extraction_ids=[],
            entity_count=0,
        )

        markdown = await report_service._generate_single_report(data, None)

        assert "# company-a - Extraction Report" in markdown

    @pytest.mark.asyncio
    async def test_generate_single_report_groups_by_category(self, report_service):
        """Test single report groups extractions by category."""
        data = ReportData(
            extractions_by_group={
                "company-a": [
                    {
                        "id": "ext-1",
                        "data": {"fact": "Fact 1"},
                        "extraction_type": "Technical",
                        "confidence": 0.95,
                    },
                    {
                        "id": "ext-2",
                        "data": {"fact": "Fact 2"},
                        "extraction_type": "Pricing",
                        "confidence": 0.90,
                    },
                ]
            },
            entities_by_group={},
            source_groups=["company-a"],
            extraction_ids=["ext-1", "ext-2"],
            entity_count=0,
        )

        markdown = await report_service._generate_single_report(data, None)

        assert "## Technical" in markdown
        assert "## Pricing" in markdown

    @pytest.mark.asyncio
    async def test_generate_single_report_includes_confidence(self, report_service):
        """Test single report includes confidence scores."""
        data = ReportData(
            extractions_by_group={
                "company-a": [
                    {
                        "id": "ext-1",
                        "data": {"fact": "Important fact"},
                        "extraction_type": "General",
                        "confidence": 0.95,
                    }
                ]
            },
            entities_by_group={},
            source_groups=["company-a"],
            extraction_ids=["ext-1"],
            entity_count=0,
        )

        markdown = await report_service._generate_single_report(data, None)

        assert "confidence: 0.95" in markdown


class TestGenerateComparisonReport:
    """Test _generate_comparison_report() method."""

    @pytest.mark.asyncio
    async def test_generate_comparison_report_has_tables(self, report_service):
        """Test comparison report includes entity tables."""
        data = ReportData(
            extractions_by_group={"company-a": [], "company-b": []},
            entities_by_group={
                "company-a": {
                    "limit": [{"id": "ent-1", "value": "API calls", "normalized_value": "api_calls", "attributes": {}}]
                },
                "company-b": {
                    "limit": [{"id": "ent-2", "value": "Storage", "normalized_value": "storage", "attributes": {}}]
                },
            },
            source_groups=["company-a", "company-b"],
            extraction_ids=[],
            entity_count=2,
        )

        markdown = await report_service._generate_comparison_report(data, None)

        assert "## Limit" in markdown
        assert "| Entity |" in markdown

    @pytest.mark.asyncio
    async def test_generate_comparison_report_includes_analysis(self, report_service):
        """Test comparison report includes detailed findings section."""
        data = ReportData(
            extractions_by_group={
                "company-a": [{"id": "ext-1", "data": {"fact": "Fact A"}, "extraction_type": "General"}],
                "company-b": [{"id": "ext-2", "data": {"fact": "Fact B"}, "extraction_type": "General"}],
            },
            entities_by_group={},
            source_groups=["company-a", "company-b"],
            extraction_ids=["ext-1", "ext-2"],
            entity_count=0,
        )

        markdown = await report_service._generate_comparison_report(data, None)

        assert "## Detailed Findings" in markdown
        assert "### company-a" in markdown
        assert "### company-b" in markdown


class TestBuildEntityTable:
    """Test _build_entity_table() method."""

    def test_build_entity_table_creates_markdown(self, report_service):
        """Test entity table generates proper markdown."""
        entities_by_group = {
            "company-a": {
                "limit": [
                    {"value": "API calls", "normalized_value": "api_calls", "attributes": {}}
                ]
            },
            "company-b": {
                "limit": [
                    {"value": "Storage", "normalized_value": "storage", "attributes": {}}
                ]
            },
        }

        table = report_service._build_entity_table("limit", entities_by_group)

        assert "| Entity | company-a | company-b |" in table
        assert "API calls" in table
        assert "Storage" in table

    def test_build_entity_table_handles_missing_values(self, report_service):
        """Test entity table shows N/A for missing values."""
        entities_by_group = {
            "company-a": {
                "limit": [
                    {"value": "API calls", "normalized_value": "api_calls", "attributes": {}}
                ]
            },
            "company-b": {
                "limit": []
            },
        }

        table = report_service._build_entity_table("limit", entities_by_group)

        assert "N/A" in table

    def test_build_entity_table_sorts_rows(self, report_service):
        """Test entity table rows are sorted alphabetically."""
        entities_by_group = {
            "company-a": {
                "limit": [
                    {"value": "Zebra", "normalized_value": "zebra", "attributes": {}},
                    {"value": "Apple", "normalized_value": "apple", "attributes": {}},
                ]
            }
        }

        table = report_service._build_entity_table("limit", entities_by_group)
        lines = table.split("\n")

        # Find the data rows (skip header and separator)
        data_rows = [line for line in lines if line.startswith("| ") and "Entity" not in line and "---" not in line]

        # Apple should come before Zebra
        assert "Apple" in data_rows[0]
        assert "Zebra" in data_rows[1]
