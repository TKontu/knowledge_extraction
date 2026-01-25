"""Report generation service."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from models import ReportRequest, ReportType
from orm_models import Report
from services.llm.client import LLMClient
from services.reports.excel_formatter import ExcelFormatter
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
    extraction_ids: list[str]  # All extraction UUIDs for provenance tracking
    entity_count: int  # Total entity count across all groups


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
        binary_content = None
        report_format = "md"

        if request.type == ReportType.TABLE:
            # Create title if not provided (need it before generation)
            title = request.title or f"Table: {' vs '.join(request.source_groups)}"

            md_content, excel_bytes = await self._generate_table_report(
                data=data,
                title=title,
                columns=request.columns,
                output_format=request.output_format,
            )
            content = md_content
            if excel_bytes:
                binary_content = excel_bytes
                report_format = "xlsx"
        elif request.type == ReportType.SCHEMA_TABLE:
            from services.reports.schema_table import SchemaTableReport

            schema_report = SchemaTableReport(self._db)
            md_content, excel_bytes = await schema_report.generate(
                project_id=project_id,
                source_groups=request.source_groups,
                output_format=request.output_format,
            )
            content = md_content
            if excel_bytes:
                binary_content = excel_bytes
                report_format = "xlsx"
            title = (
                request.title or f"Schema Report: {', '.join(request.source_groups)}"
            )
        elif request.type == ReportType.SINGLE:
            content = await self._generate_single_report(data, request.title)
            title = request.title or f"{request.source_groups[0]} - Extraction Report"
        else:
            content = await self._generate_comparison_report(data, request.title)
            title = request.title or f"Comparison: {' vs '.join(request.source_groups)}"

        # Create and save report with provenance tracking
        report = Report(
            project_id=project_id,
            type=request.type.value,
            title=title,
            content=content,
            source_groups=request.source_groups,
            categories=request.categories or [],
            extraction_ids=data.extraction_ids,
            format=report_format,
            binary_content=binary_content,
            meta_data={"entity_count": data.entity_count},
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
            ReportData with aggregated data including provenance info
        """
        extractions_by_group: dict[str, list[dict]] = {}
        entities_by_group: dict[str, dict[str, list[dict]]] = {}
        all_extraction_ids: list[str] = []
        total_entity_count = 0

        # Gather extractions for each source group
        for source_group in source_groups:
            filters = ExtractionFilters(
                project_id=project_id,
                source_group=source_group,
            )
            extractions = await self._extraction_repo.list(
                filters=filters, limit=max_extractions, offset=0, include_source=True
            )
            extractions_by_group[source_group] = [
                {
                    "id": str(ext.id),
                    "data": ext.data,
                    "confidence": ext.confidence,
                    "extraction_type": ext.extraction_type,
                    "source_id": str(ext.source_id),
                    "source_uri": ext.source.uri if ext.source else None,
                    "source_title": ext.source.title if ext.source else None,
                    "chunk_index": ext.chunk_index,
                }
                for ext in extractions
            ]
            # Track all extraction IDs for provenance
            all_extraction_ids.extend(str(ext.id) for ext in extractions)

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
                            "id": str(ent.id),
                            "value": ent.value,
                            "normalized_value": ent.normalized_value,
                            "attributes": ent.attributes,
                        }
                        for ent in entities
                    ]
                    total_entity_count += len(entities)

        return ReportData(
            extractions_by_group=extractions_by_group,
            entities_by_group=entities_by_group,
            source_groups=source_groups,
            extraction_ids=all_extraction_ids,
            entity_count=total_entity_count,
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
        max_detail_extractions = 10
        lines.append("## Detailed Findings")
        lines.append("")

        for source_group in data.source_groups:
            extractions = data.extractions_by_group.get(source_group, [])
            lines.append(f"### {source_group}")
            lines.append("")

            for ext in extractions[:max_detail_extractions]:
                data_dict = ext.get("data", {})
                fact = data_dict.get("fact", str(data_dict))
                lines.append(f"- {fact}")

            # Note if extractions were truncated
            if len(extractions) > max_detail_extractions:
                lines.append("")
                lines.append(
                    f"*Showing {max_detail_extractions} of {len(extractions)} extractions*"
                )

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

    def _humanize(self, field_name: str) -> str:
        """Convert field_name to Human Readable Label."""
        return field_name.replace("_", " ").title()

    async def _aggregate_for_table(
        self,
        data: ReportData,
        columns: list[str] | None,
    ) -> tuple[list[dict], list[str]]:
        """Aggregate extractions into table rows.

        For each source_group, consolidate multiple extractions
        into a single row.

        Args:
            data: Report data with extractions by group
            columns: Specific columns to include, or None for all

        Returns:
            Tuple of (rows list, columns list)
        """
        rows = []
        all_columns: set[str] = set()

        for source_group in data.source_groups:
            extractions = data.extractions_by_group.get(source_group, [])
            row: dict = {"source_group": source_group}

            # Collect all field values from extractions
            field_values: dict[str, list] = {}
            for ext in extractions:
                ext_data = ext.get("data", {})
                for field, value in ext_data.items():
                    if field not in field_values:
                        field_values[field] = []
                    if value is not None:
                        field_values[field].append(value)
                        all_columns.add(field)

            # Aggregate values per field
            for field, values in field_values.items():
                if not values:
                    row[field] = None
                elif isinstance(values[0], bool):
                    # Use any() for booleans - True if ANY extraction says True
                    # (e.g., "manufactures motors" should be True if mentioned anywhere)
                    row[field] = any(values)
                elif isinstance(values[0], (int, float)):
                    # Use max for numbers
                    row[field] = max(values)
                elif isinstance(values[0], list):
                    # Flatten and dedupe lists
                    flat: list = []
                    for v in values:
                        flat.extend(v)
                    row[field] = list(dict.fromkeys(flat))
                else:
                    # For text, take longest non-empty
                    row[field] = max(values, key=len) if values else None

            rows.append(row)

        # Determine column order
        final_columns = ["source_group"]
        if columns:
            final_columns.extend(c for c in columns if c in all_columns)
        else:
            final_columns.extend(sorted(all_columns))

        return rows, final_columns

    def _build_markdown_table(
        self,
        rows: list[dict],
        columns: list[str],
        title: str | None,
    ) -> str:
        """Build markdown table from rows."""
        lines = []
        if title:
            lines.append(f"# {title}")
            lines.append("")
            lines.append(
                f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            lines.append("")

        # Column labels
        labels = [self._humanize(c) for c in columns]
        lines.append("| " + " | ".join(labels) + " |")
        lines.append("|" + "|".join(["---"] * len(columns)) + "|")

        # Data rows
        for row in rows:
            values = []
            for col in columns:
                val = row.get(col)
                if val is None:
                    values.append("N/A")
                elif isinstance(val, bool):
                    values.append("Yes" if val else "No")
                elif isinstance(val, list):
                    values.append(", ".join(str(v) for v in val))
                else:
                    values.append(str(val))
            lines.append("| " + " | ".join(values) + " |")

        return "\n".join(lines)

    async def _generate_table_report(
        self,
        data: ReportData,
        title: str | None,
        columns: list[str] | None,
        output_format: str,
    ) -> tuple[str, bytes | None]:
        """Generate table report in markdown or Excel.

        Args:
            data: Aggregated report data
            title: Report title
            columns: Fields to include as columns
            output_format: Output format ("md" or "xlsx")

        Returns:
            Tuple of (markdown_content, excel_bytes or None)
        """
        rows, final_columns = await self._aggregate_for_table(data, columns)

        md_content = self._build_markdown_table(rows, final_columns, title)

        if output_format == "xlsx":
            formatter = ExcelFormatter()
            excel_bytes = formatter.create_workbook(
                rows=rows,
                columns=final_columns,
                sheet_name=title or "Company Comparison",
            )
            return md_content, excel_bytes

        return md_content, None
