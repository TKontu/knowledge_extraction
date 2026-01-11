"""Report generation service."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from models import ReportRequest, ReportType
from orm_models import Report
from services.llm.client import LLMClient
from services.storage.repositories.entity import EntityFilters, EntityRepository
from services.storage.repositories.extraction import (
    ExtractionFilters,
    ExtractionRepository,
)


@dataclass
class ReportData:
    """Aggregated data for report generation."""

    extractions_by_group: dict[str, list[dict]]
    entities_by_group: dict[str, dict[str, list[dict]]]  # group -> type -> entities
    source_groups: list[str]


class ReportService:
    """Service for generating reports from extracted data."""

    def __init__(
        self,
        extraction_repo: ExtractionRepository,
        entity_repo: EntityRepository,
        llm_client: LLMClient,
        db_session,
    ):
        """Initialize with dependencies.

        Args:
            extraction_repo: Repository for querying extractions
            entity_repo: Repository for querying entities
            llm_client: LLM client for generating summaries
            db_session: SQLAlchemy database session
        """
        self._extraction_repo = extraction_repo
        self._entity_repo = entity_repo
        self._llm_client = llm_client
        self._db = db_session

    async def generate(
        self,
        project_id: UUID,
        request: ReportRequest,
    ) -> Report:
        """Generate a report based on the request.

        Args:
            project_id: Project UUID
            request: Report request parameters

        Returns:
            Generated Report ORM object
        """
        # Gather data
        data = await self._gather_data(
            project_id=project_id,
            source_groups=request.source_groups,
            categories=request.categories,
            entity_types=request.entity_types,
            max_extractions=request.max_extractions,
        )

        # Generate markdown content based on report type
        if request.type == ReportType.SINGLE:
            content = await self._generate_single_report(data, request.title)
        else:
            content = await self._generate_comparison_report(data, request.title)

        # Create title if not provided
        if not request.title:
            if request.type == ReportType.SINGLE:
                title = f"{request.source_groups[0]} - Extraction Report"
            else:
                title = f"Comparison: {' vs '.join(request.source_groups)}"
        else:
            title = request.title

        # Create and save report
        report = Report(
            project_id=project_id,
            type=request.type.value,
            title=title,
            content=content,
            source_groups=request.source_groups,
            categories=request.categories or [],
            extraction_ids=[],
            format="md",
        )

        self._db.add(report)
        self._db.commit()
        self._db.refresh(report)

        return report

    async def _gather_data(
        self,
        project_id: UUID,
        source_groups: list[str],
        categories: list[str] | None,
        entity_types: list[str] | None,
        max_extractions: int,
    ) -> ReportData:
        """Gather extractions and entities for the report.

        Args:
            project_id: Project UUID
            source_groups: List of source groups to include
            categories: Optional category filters
            entity_types: Optional entity types to fetch
            max_extractions: Max extractions per source group

        Returns:
            ReportData with aggregated data
        """
        extractions_by_group: dict[str, list[dict]] = {}
        entities_by_group: dict[str, dict[str, list[dict]]] = {}

        # Gather extractions for each source group
        for source_group in source_groups:
            filters = ExtractionFilters(
                project_id=project_id,
                source_group=source_group,
            )
            extractions = await self._extraction_repo.list(
                filters=filters, limit=max_extractions, offset=0
            )
            extractions_by_group[source_group] = [
                {
                    "data": ext.data,
                    "confidence": ext.confidence,
                    "extraction_type": ext.extraction_type,
                }
                for ext in extractions
            ]

        # Gather entities for each source group
        for source_group in source_groups:
            entities_by_group[source_group] = {}

            if entity_types:
                for entity_type in entity_types:
                    filters = EntityFilters(
                        project_id=project_id,
                        source_group=source_group,
                        entity_type=entity_type,
                    )
                    entities = await self._entity_repo.list(filters=filters)
                    entities_by_group[source_group][entity_type] = [
                        {
                            "value": ent.value,
                            "normalized_value": ent.normalized_value,
                            "attributes": ent.attributes,
                        }
                        for ent in entities
                    ]

        return ReportData(
            extractions_by_group=extractions_by_group,
            entities_by_group=entities_by_group,
            source_groups=source_groups,
        )

    async def _generate_single_report(
        self,
        data: ReportData,
        title: str | None,
    ) -> str:
        """Generate markdown for single source_group report.

        Args:
            data: Aggregated report data
            title: Optional custom title

        Returns:
            Markdown content
        """
        source_group = data.source_groups[0]
        extractions = data.extractions_by_group.get(source_group, [])

        # Build title
        if not title:
            title = f"{source_group} - Extraction Report"

        # Start building markdown
        lines = [
            f"# {title}",
            "",
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Extractions: {len(extractions)}",
            "",
        ]

        # Group extractions by category/type
        by_category: dict[str, list[dict]] = {}
        for ext in extractions:
            category = ext.get("extraction_type", "General")
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(ext)

        # Add sections by category
        for category, items in sorted(by_category.items()):
            lines.append(f"## {category}")
            lines.append("")
            for item in items:
                confidence = item.get("confidence")
                data_dict = item.get("data", {})
                fact = data_dict.get("fact", str(data_dict))
                if confidence is not None:
                    lines.append(f"- {fact} (confidence: {confidence:.2f})")
                else:
                    lines.append(f"- {fact}")
            lines.append("")

        # Add sources footer
        lines.append("## Sources")
        lines.append(f"Based on extractions from {source_group}.")

        return "\n".join(lines)

    async def _generate_comparison_report(
        self,
        data: ReportData,
        title: str | None,
    ) -> str:
        """Generate markdown for comparison report with entity tables.

        Args:
            data: Aggregated report data
            title: Optional custom title

        Returns:
            Markdown content
        """
        # Build title
        if not title:
            title = f"Comparison: {' vs '.join(data.source_groups)}"

        # Start building markdown
        lines = [
            f"# {title}",
            "",
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
        ]

        # Add entity comparison tables
        if data.entities_by_group:
            # Get all entity types across all groups
            entity_types = set()
            for group_entities in data.entities_by_group.values():
                entity_types.update(group_entities.keys())

            for entity_type in sorted(entity_types):
                table = self._build_entity_table(entity_type, data.entities_by_group)
                if table:
                    lines.append(f"## {entity_type.title()}")
                    lines.append(table)
                    lines.append("")

        # Add detailed findings
        lines.append("## Detailed Findings")
        lines.append("")

        for source_group in data.source_groups:
            extractions = data.extractions_by_group.get(source_group, [])
            lines.append(f"### {source_group}")
            lines.append("")

            for ext in extractions[:10]:  # Limit to top 10 per group
                data_dict = ext.get("data", {})
                fact = data_dict.get("fact", str(data_dict))
                lines.append(f"- {fact}")

            lines.append("")

        return "\n".join(lines)

    def _build_entity_table(
        self,
        entity_type: str,
        entities_by_group: dict[str, dict[str, list[dict]]],
    ) -> str:
        """Build markdown table from entity data.

        Args:
            entity_type: Type of entity (e.g., "limit", "pricing")
            entities_by_group: Dict of group -> type -> entities

        Returns:
            Markdown table string
        """
        # Collect all unique entity values (rows)
        all_values: set[str] = set()
        for _group, types in entities_by_group.items():
            if entity_type in types:
                for ent in types[entity_type]:
                    all_values.add(ent["value"])

        if not all_values:
            return ""

        # Build table header
        groups = list(entities_by_group.keys())
        header = "| Entity | " + " | ".join(groups) + " |"
        separator = "|--------|" + "|".join(["--------"] * len(groups)) + "|"

        # Build table rows
        rows = []
        for value in sorted(all_values):
            row_data = [value]
            for group in groups:
                # Find matching entity in this group
                found = False
                if entity_type in entities_by_group.get(group, {}):
                    for ent in entities_by_group[group][entity_type]:
                        if ent["value"] == value:
                            row_data.append("Yes")
                            found = True
                            break
                if not found:
                    row_data.append("N/A")

            rows.append("| " + " | ".join(row_data) + " |")

        return "\n".join([header, separator] + rows)
