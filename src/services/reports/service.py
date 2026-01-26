"""Report generation service."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from models import ReportRequest, ReportType
from orm_models import Report
from services.llm.client import LLMClient
from services.projects.repository import ProjectRepository
from services.reports.excel_formatter import ExcelFormatter
from services.reports.schema_table_generator import SchemaTableGenerator
from services.reports.synthesis import ReportSynthesizer, SynthesisResult
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
        synthesizer: ReportSynthesizer | None = None,
        project_repo: ProjectRepository | None = None,
    ):
        """Initialize with dependencies.

        Args:
            extraction_repo: Repository for querying extractions
            entity_repo: Repository for querying entities
            llm_client: LLM client for generating summaries
            db_session: SQLAlchemy database session
            synthesizer: Optional ReportSynthesizer for LLM-based synthesis
            project_repo: Optional ProjectRepository for loading project schemas
        """
        self._extraction_repo = extraction_repo
        self._entity_repo = entity_repo
        self._llm_client = llm_client
        self._db = db_session
        # Create synthesizer if not provided (for backward compatibility)
        self._synthesizer = synthesizer or ReportSynthesizer(llm_client)
        # Create project repo and schema generator for table reports
        self._project_repo = project_repo or ProjectRepository(db_session)
        self._schema_generator = SchemaTableGenerator()

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
                project_id=project_id,  # Pass project_id for schema-driven columns
            )
            content = md_content
            if excel_bytes:
                binary_content = excel_bytes
                report_format = "xlsx"
        elif request.type == ReportType.SCHEMA_TABLE:
            # DEPRECATED: Use TABLE instead - it now derives columns from schema
            import warnings

            warnings.warn(
                "SCHEMA_TABLE is deprecated, use TABLE instead. "
                "TABLE now derives columns from project schema.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Forward to TABLE implementation
            title = (
                request.title or f"Schema Report: {', '.join(request.source_groups)}"
            )
            md_content, excel_bytes = await self._generate_table_report(
                data=data,
                title=title,
                columns=request.columns,
                output_format=request.output_format,
                project_id=project_id,
            )
            content = md_content
            if excel_bytes:
                binary_content = excel_bytes
                report_format = "xlsx"
        elif request.type == ReportType.SINGLE:
            content = await self._generate_single_report(data, request.title)
            title = request.title or f"{request.source_groups[0]} - Extraction Report"
        else:
            content = await self._generate_comparison_report(
                data, request.title, request.max_detail_extractions
            )
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
        """Generate markdown for single source_group report with LLM synthesis.

        Args:
            data: Aggregated report data
            title: Optional custom title

        Returns:
            Markdown content
        """
        source_group = data.source_groups[0]
        extractions = data.extractions_by_group.get(source_group, [])

        if not title:
            title = f"{source_group} - Extraction Report"

        lines = [
            f"# {title}",
            "",
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Extractions: {len(extractions)}",
            "",
        ]

        # Group by extraction_type
        by_category: dict[str, list[dict]] = {}
        for ext in extractions:
            category = ext.get("extraction_type", "General")
            by_category.setdefault(category, []).append(ext)

        # Synthesize each category (skip LLM for single-fact categories)
        sources_referenced: set[str] = set()
        for category, items in sorted(by_category.items()):
            if len(items) <= 1:
                # Skip LLM synthesis for single facts - just format directly
                if items:
                    item = items[0]
                    fact_text = item.get("data", {}).get(
                        "fact", str(item.get("data", ""))
                    )
                    source_title = item.get("source_title", "")
                    source_uri = item.get("source_uri")
                    text = (
                        f"{fact_text} [Source: {source_title}]"
                        if source_title
                        else fact_text
                    )
                    result = SynthesisResult(
                        synthesized_text=text,
                        sources_used=[source_uri] if source_uri else [],
                        confidence=item.get("confidence", 0.9),
                        conflicts_noted=[],
                    )
                else:
                    result = SynthesisResult(
                        synthesized_text="No facts available.",
                        sources_used=[],
                        confidence=0.0,
                        conflicts_noted=[],
                    )
            else:
                # Use LLM synthesis for multiple facts
                result = await self._synthesizer.synthesize_facts(
                    items, synthesis_type="summarize"
                )

            lines.append(f"## {category}")
            lines.append("")
            lines.append(result.synthesized_text)
            lines.append("")
            sources_referenced.update(result.sources_used)

            # Note conflicts if any
            if result.conflicts_noted:
                lines.append("*Note: " + "; ".join(result.conflicts_noted) + "*")
                lines.append("")

        # Add sources section
        lines.append("## Sources Referenced")
        lines.append("")
        for ext in extractions:
            uri = ext.get("source_uri")
            title_text = ext.get("source_title", uri)
            if uri and uri in sources_referenced:
                lines.append(f"- [{title_text}]({uri})")

        return "\n".join(lines)

    async def _generate_comparison_report(
        self,
        data: ReportData,
        title: str | None,
        max_detail_extractions: int = 10,
    ) -> str:
        """Generate markdown for comparison report with entity tables.

        Args:
            data: Aggregated report data
            title: Optional custom title
            max_detail_extractions: Max extractions per source group in findings

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

        # Add detailed findings with source attribution
        lines.append("## Detailed Findings")
        lines.append("")

        all_sources_referenced: set[str] = set()

        for source_group in data.source_groups:
            extractions = data.extractions_by_group.get(source_group, [])
            lines.append(f"### {source_group}")
            lines.append("")

            for ext in extractions[:max_detail_extractions]:
                data_dict = ext.get("data", {})
                fact = data_dict.get("fact", str(data_dict))
                source_title = ext.get("source_title", "")
                source_uri = ext.get("source_uri", "")

                if source_title:
                    lines.append(f"- {fact} [Source: {source_title}]")
                    if source_uri:
                        all_sources_referenced.add(source_uri)
                else:
                    lines.append(f"- {fact}")

            # Note if extractions were truncated
            if len(extractions) > max_detail_extractions:
                lines.append("")
                lines.append(
                    f"*Showing {max_detail_extractions} of {len(extractions)} extractions*"
                )

            lines.append("")

        # Add sources section for comparison report
        if all_sources_referenced:
            lines.append("## Sources Referenced")
            lines.append("")
            all_extractions = [
                ext
                for group_exts in data.extractions_by_group.values()
                for ext in group_exts
            ]
            uri_to_title = {}
            for ext in all_extractions:
                if ext.get("source_uri") and ext.get("source_title"):
                    uri_to_title[ext["source_uri"]] = ext["source_title"]

            for uri in sorted(all_sources_referenced):
                title = uri_to_title.get(uri, uri)
                lines.append(f"- [{title}]({uri})")
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

    async def _get_project_schema(self, project_id: UUID) -> dict | None:
        """Load project's extraction_schema.

        Args:
            project_id: UUID of the project.

        Returns:
            The extraction_schema dict, or None if project not found.
        """
        project = await self._project_repo.get(project_id)
        return project.extraction_schema if project else None

    async def _aggregate_for_table(
        self,
        data: ReportData,
        columns: list[str] | None,
        extraction_schema: dict | None = None,
    ) -> tuple[list[dict], list[str], dict[str, str]]:
        """Aggregate extractions into table rows.

        For each source_group, consolidate multiple extractions
        into a single row. When extraction_schema is provided, derives
        columns and labels from the schema for template-agnostic output.

        Args:
            data: Report data with extractions by group
            columns: Specific columns to include, or None for all
            extraction_schema: Optional project schema for column/label derivation

        Returns:
            Tuple of (rows list, columns list, labels dict)
        """
        rows = []
        all_columns: set[str] = set()

        # Get schema-derived info if available
        if extraction_schema:
            try:
                schema_columns, labels, _field_defs = (
                    self._schema_generator.get_columns_from_schema(extraction_schema)
                )
                entity_list_groups = self._schema_generator.get_entity_list_groups(
                    extraction_schema
                )
            except (KeyError, TypeError) as e:
                # Malformed schema - fall back to non-schema mode
                import structlog

                logger = structlog.get_logger(__name__)
                logger.warning(
                    "malformed_extraction_schema_fallback",
                    error=str(e),
                )
                schema_columns = None
                labels = {}
                entity_list_groups = {}
        else:
            schema_columns = None
            labels = {}
            entity_list_groups = {}

        for source_group in data.source_groups:
            extractions = data.extractions_by_group.get(source_group, [])
            row: dict = {"source_group": source_group}

            # Group extractions by type for entity list handling
            by_type: dict[str, list[dict]] = {}
            for ext in extractions:
                ext_type = ext.get("extraction_type", "general")
                by_type.setdefault(ext_type, []).append(ext.get("data", {}))

            # Process entity lists (e.g., products)
            for group_name, field_group in entity_list_groups.items():
                col_name = f"{group_name}_list"
                items: list[dict] = []
                for data_dict in by_type.get(group_name, []):
                    # Entity list data has items under various keys
                    for key in ["products", "items", group_name, "entities", "list"]:
                        if key in data_dict and isinstance(data_dict[key], list):
                            items.extend(data_dict[key])
                            break
                    else:
                        # If no list key found but data_dict looks like an entity
                        if data_dict and not any(
                            k in data_dict
                            for k in ["products", "items", "entities", "list"]
                        ):
                            items.append(data_dict)
                row[col_name] = self._schema_generator.format_entity_list(
                    items, field_group
                )
                all_columns.add(col_name)

            # Collect all field values from extractions (skip entity list types)
            field_values: dict[str, list] = {}
            for ext in extractions:
                ext_type = ext.get("extraction_type", "")
                # Skip entity_list extractions - already handled above
                if ext_type in entity_list_groups:
                    continue
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
                    # For text, dedupe and concatenate unique values
                    unique_texts = list(
                        dict.fromkeys(str(v) for v in values if v)
                    )
                    if len(unique_texts) > 1:
                        row[field] = "; ".join(unique_texts)
                    elif unique_texts:
                        row[field] = unique_texts[0]
                    else:
                        row[field] = None

            rows.append(row)

        # Determine column order
        final_columns = ["source_group"]
        if columns:
            # User-specified columns
            final_columns.extend(c for c in columns if c in all_columns)
        elif schema_columns:
            # Schema-derived columns (preserve schema order)
            final_columns.extend(
                c for c in schema_columns if c in all_columns or c == "source_group"
            )
            # Remove duplicates while preserving order
            seen = set()
            final_columns = [
                c for c in final_columns if not (c in seen or seen.add(c))
            ]
        else:
            # Fallback: alphabetical
            final_columns.extend(sorted(all_columns))

        # Build final labels dict
        final_labels = {"source_group": "Source"}
        for col in final_columns:
            if col in labels:
                final_labels[col] = labels[col]
            elif col not in final_labels:
                final_labels[col] = self._humanize(col)

        return rows, final_columns, final_labels

    def _build_markdown_table(
        self,
        rows: list[dict],
        columns: list[str],
        title: str | None,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Build markdown table from rows.

        Args:
            rows: List of row dicts.
            columns: Column names in order.
            title: Optional report title.
            labels: Optional column name to label mapping.

        Returns:
            Markdown table string.
        """
        lines = []
        if title:
            lines.append(f"# {title}")
            lines.append("")
            lines.append(
                f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            lines.append("")

        # Column labels - use provided labels or humanize
        header_labels = [
            labels.get(c, self._humanize(c)) if labels else self._humanize(c)
            for c in columns
        ]
        lines.append("| " + " | ".join(header_labels) + " |")
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
        project_id: UUID | None = None,
    ) -> tuple[str, bytes | None]:
        """Generate table report in markdown or Excel.

        When project_id is provided, derives columns and labels from the
        project's extraction_schema for template-agnostic output.

        Args:
            data: Aggregated report data
            title: Report title
            columns: Fields to include as columns
            output_format: Output format ("md" or "xlsx")
            project_id: Optional project ID for schema-driven columns/labels

        Returns:
            Tuple of (markdown_content, excel_bytes or None)
        """
        # Load schema if project_id provided
        extraction_schema = None
        if project_id:
            extraction_schema = await self._get_project_schema(project_id)

        rows, final_columns, labels = await self._aggregate_for_table(
            data, columns, extraction_schema
        )

        md_content = self._build_markdown_table(rows, final_columns, title, labels)

        if output_format == "xlsx":
            formatter = ExcelFormatter()
            excel_bytes = formatter.create_workbook(
                rows=rows,
                columns=final_columns,
                column_labels=labels,
                sheet_name=title or "Comparison",
            )
            return md_content, excel_bytes

        return md_content, None
