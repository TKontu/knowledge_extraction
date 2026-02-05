"""Report generation service."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import structlog

from config import settings
from models import ReportRequest, ReportType
from orm_models import Extraction, Report
from services.llm.client import LLMClient
from services.projects.repository import ProjectRepository
from services.reports.excel_formatter import ExcelFormatter
from services.reports.schema_table_generator import ColumnMetadata, SchemaTableGenerator
from services.reports.smart_merge import MergeCandidate, SmartMergeService
from services.reports.synthesis import ReportSynthesizer, SynthesisResult
from services.storage.repositories.entity import EntityFilters, EntityRepository
from services.storage.repositories.extraction import (
    ExtractionFilters,
    ExtractionRepository,
)

logger = structlog.get_logger(__name__)


@dataclass
class ReportData:
    """Aggregated data for report generation."""

    extractions_by_group: dict[str, list[dict]]
    entities_by_group: dict[str, dict[str, list[dict]]]  # group -> type -> entities
    source_groups: list[str]
    extraction_ids: list[str]  # All extraction UUIDs for provenance tracking
    entity_count: int  # Total entity count across all groups
    # Extractions grouped by source_id for per-URL aggregation
    extractions_by_source: dict[str, list[dict]] = field(default_factory=dict)


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

    def _get_all_source_groups(self, project_id: UUID) -> list[str]:
        """Get all distinct source groups for a project.

        Args:
            project_id: Project UUID

        Returns:
            List of source group names
        """
        results = (
            self._db.query(Extraction.source_group)
            .filter(Extraction.project_id == project_id)
            .distinct()
            .all()
        )
        return [r[0] for r in results]

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
        # Resolve source_groups - if None or empty, get all from project
        source_groups = request.source_groups
        if not source_groups:
            source_groups = self._get_all_source_groups(project_id)
            logger.info(
                "Using all source groups",
                project_id=str(project_id),
                count=len(source_groups),
            )

        # Gather data
        data = self._gather_data(
            project_id=project_id,
            source_groups=source_groups,
            categories=request.categories,
            entity_types=request.entity_types,
            max_extractions=request.max_extractions,
        )

        # Generate markdown content based on report type
        binary_content = None
        report_format = "md"

        if request.type == ReportType.TABLE:
            # Create title if not provided (need it before generation)
            title = request.title or f"Table: {len(source_groups)} companies"

            md_content, excel_bytes = await self._generate_table_report(
                data=data,
                title=title,
                columns=request.columns,
                output_format=request.output_format,
                project_id=project_id,
                group_by=request.group_by,
                include_merge_metadata=request.include_merge_metadata,
            )
            content = md_content
            if excel_bytes:
                binary_content = excel_bytes
                report_format = "xlsx"
        elif request.type == ReportType.SINGLE:
            content = await self._generate_single_report(data, request.title)
            title = request.title or f"{source_groups[0]} - Extraction Report"
        else:
            content = self._generate_comparison_report(
                data, request.title, request.max_detail_extractions
            )
            title = request.title or f"Comparison: {len(source_groups)} companies"

        # Create and save report with provenance tracking
        report = Report(
            project_id=project_id,
            type=request.type.value,
            title=title,
            content=content,
            source_groups=source_groups,
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

    def _gather_data(
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
        extractions_by_source: dict[str, list[dict]] = {}
        entities_by_group: dict[str, dict[str, list[dict]]] = {}
        all_extraction_ids: list[str] = []
        total_entity_count = 0

        # Gather extractions for each source group
        for source_group in source_groups:
            filters = ExtractionFilters(
                project_id=project_id,
                source_group=source_group,
            )
            extractions = self._extraction_repo.list(
                filters=filters, limit=max_extractions, offset=0, include_source=True
            )

            extraction_dicts = []
            for ext in extractions:
                ext_dict = {
                    "id": str(ext.id),
                    "data": ext.data,
                    "confidence": ext.confidence,
                    "extraction_type": ext.extraction_type,
                    "source_id": str(ext.source_id),
                    "source_uri": ext.source.uri if ext.source else None,
                    "source_title": ext.source.title if ext.source else None,
                    "chunk_index": ext.chunk_index,
                }
                extraction_dicts.append(ext_dict)

                # Also group by source_id for per-URL aggregation
                source_id = str(ext.source_id)
                if source_id not in extractions_by_source:
                    extractions_by_source[source_id] = []
                extractions_by_source[source_id].append(ext_dict)

            extractions_by_group[source_group] = extraction_dicts
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
                    entities = self._entity_repo.list(filters=filters)
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
            extractions_by_source=extractions_by_source,
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

    def _generate_comparison_report(
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

    def _get_project_schema(self, project_id: UUID) -> dict | None:
        """Load project's extraction_schema.

        Args:
            project_id: UUID of the project.

        Returns:
            The extraction_schema dict, or None if project not found.
        """
        project = self._project_repo.get(project_id)
        return project.extraction_schema if project else None

    def _aggregate_by_source(
        self,
        data: ReportData,
        extraction_schema: dict,
    ) -> tuple[list[dict], list[str], dict[str, str], dict[str, ColumnMetadata]]:
        """Aggregate extractions into one row per source (URL).

        Consolidates all field group extractions from a single URL into one row.

        Args:
            data: Report data with extractions by group and by source.
            extraction_schema: Project extraction schema for column derivation.

        Returns:
            Tuple of (rows, columns, labels, column_metadata).
        """
        # Get flattened columns from schema
        columns, labels, col_metadata = (
            self._schema_generator.get_flattened_columns_for_source(extraction_schema)
        )

        # Get extraction_type to field mapping for flattening
        type_to_fields = self._schema_generator.get_extraction_type_to_fields(
            extraction_schema
        )

        rows = []

        # Group extractions by source_id
        for source_id, extractions in data.extractions_by_source.items():
            if not extractions:
                continue

            # Get source metadata from first extraction
            first_ext = extractions[0]
            source_url = first_ext.get("source_uri") or ""
            source_title = first_ext.get("source_title") or ""

            # Extract domain from URL
            try:
                parsed = urlparse(source_url)
                domain = parsed.netloc or ""
            except Exception:
                domain = ""

            row: dict = {
                "source_url": source_url,
                "source_title": source_title,
                "domain": domain,
            }

            # Collect confidences for averaging and per-column tracking
            confidences = []
            # Track confidence per column for accurate domain merge filtering
            column_confidences: dict[str, float] = {}

            # Flatten all extractions for this source into the row
            for ext in extractions:
                ext_type = ext.get("extraction_type", "")
                ext_data = ext.get("data", {})
                ext_confidence = ext.get("confidence")

                if ext_confidence is not None:
                    confidences.append(ext_confidence)

                # Map extraction data to flattened columns
                field_mapping = type_to_fields.get(ext_type, [])

                # Check if this is an entity list extraction
                entity_list_groups = self._schema_generator.get_entity_list_groups(
                    extraction_schema
                )
                if ext_type in entity_list_groups:
                    # Entity list - format the items
                    items = []
                    for key in ["products", "items", ext_type, "entities", "list"]:
                        if key in ext_data and isinstance(ext_data[key], list):
                            items.extend(ext_data[key])
                            break
                    else:
                        # Data might be the entity itself
                        if ext_data and not any(
                            k in ext_data
                            for k in ["products", "items", "entities", "list"]
                        ):
                            items.append(ext_data)

                    col_name = ext_type  # Entity list column name = group name
                    if items:
                        field_group = entity_list_groups[ext_type]
                        row[col_name] = self._schema_generator.format_entity_list(
                            items, field_group
                        )
                        # Track this extraction's confidence for the entity list column
                        if ext_confidence is not None:
                            column_confidences[col_name] = ext_confidence
                else:
                    # Regular field group - copy fields
                    for col_name in field_mapping:
                        # Handle prefixed columns (group.field)
                        if "." in col_name:
                            _, field_name = col_name.split(".", 1)
                        else:
                            field_name = col_name

                        if field_name in ext_data:
                            row[col_name] = ext_data[field_name]
                            # Track this extraction's confidence for the column
                            if ext_confidence is not None:
                                column_confidences[col_name] = ext_confidence

            # Calculate average confidence
            if confidences:
                row["avg_confidence"] = sum(confidences) / len(confidences)
            else:
                row["avg_confidence"] = None

            # Store per-column confidences for domain merge
            row["_column_confidences"] = column_confidences

            rows.append(row)

        return rows, columns, labels, col_metadata

    async def _aggregate_by_domain(
        self,
        data: ReportData,
        extraction_schema: dict,
        include_merge_metadata: bool = False,
    ) -> tuple[list[dict], list[str], dict[str, str]]:
        """Aggregate extractions into one row per domain with LLM smart merge.

        First aggregates by source (URL), then merges all URLs of a domain
        using LLM-based column-by-column synthesis.

        Args:
            data: Report data with extractions.
            extraction_schema: Project extraction schema.
            include_merge_metadata: Whether to include merge provenance.

        Returns:
            Tuple of (rows, columns, labels).
        """
        # First get per-source rows
        source_rows, columns, labels, col_metadata = self._aggregate_by_source(
            data, extraction_schema
        )

        if not source_rows:
            return [], columns, labels

        # Group source rows by domain
        rows_by_domain: dict[str, list[dict]] = {}
        for row in source_rows:
            domain = row.get("domain", "unknown")
            if domain not in rows_by_domain:
                rows_by_domain[domain] = []
            rows_by_domain[domain].append(row)

        # Create smart merge service with config settings
        merge_service = SmartMergeService(
            self._llm_client,
            max_candidates=settings.smart_merge_max_candidates,
            min_confidence=settings.smart_merge_min_confidence,
        )

        # Columns to merge (skip metadata columns and internal tracking)
        merge_columns = [
            c for c in columns
            if c not in ("source_url", "source_title", "domain", "avg_confidence")
            and not c.startswith("_")  # Exclude internal fields like _column_confidences
        ]

        # Merge each domain
        domain_rows = []
        for domain, domain_source_rows in rows_by_domain.items():
            domain_row: dict = {"domain": domain}

            # If single source, no merge needed
            if len(domain_source_rows) == 1:
                domain_row.update(domain_source_rows[0])
                domain_row.pop("source_url", None)
                domain_row.pop("source_title", None)
                domain_row.pop("_column_confidences", None)  # Remove internal tracking
                domain_rows.append(domain_row)
                continue

            # Merge each column in parallel
            async def merge_column(col_name: str) -> tuple[str, Any, float, dict | None]:
                """Merge a single column for this domain."""

                def get_column_confidence(row: dict, col: str) -> float | None:
                    """Get per-column confidence, falling back to avg if not available."""
                    col_conf = row.get("_column_confidences", {}).get(col)
                    if col_conf is not None:
                        return col_conf
                    return row.get("avg_confidence")

                candidates = [
                    MergeCandidate(
                        value=row.get(col_name),
                        source_url=row.get("source_url", ""),
                        source_title=row.get("source_title"),
                        # Use per-column confidence if available, otherwise fall back to avg
                        confidence=get_column_confidence(row, col_name),
                    )
                    for row in domain_source_rows
                ]

                col_meta = col_metadata.get(col_name)
                if not col_meta:
                    # Fallback metadata
                    col_meta = ColumnMetadata(
                        name=col_name,
                        label=labels.get(col_name, col_name),
                        field_type="text",
                        description=col_name,
                        field_group="unknown",
                    )

                result = await merge_service.merge_column(
                    col_name, col_meta, candidates
                )

                merge_meta = None
                if include_merge_metadata:
                    merge_meta = {
                        "confidence": result.confidence,
                        "sources_used": result.sources_used,
                        "reasoning": result.reasoning,
                    }

                # Always return confidence for avg calculation
                return col_name, result.value, result.confidence, merge_meta

            # Run all column merges in parallel
            merge_results = await asyncio.gather(
                *[merge_column(col) for col in merge_columns],
                return_exceptions=True,
            )

            # Build domain row from merge results
            merge_metadata_all = {}
            confidences = []
            for i, result in enumerate(merge_results):
                # Handle any exceptions from individual merges
                if isinstance(result, Exception):
                    col_name = merge_columns[i]
                    logger.warning(
                        "column_merge_failed",
                        domain=domain,
                        column=col_name,
                        error=str(result),
                    )
                    domain_row[col_name] = None
                    continue

                col_name, value, confidence, merge_meta = result
                domain_row[col_name] = value
                if confidence is not None:
                    confidences.append(confidence)
                if merge_meta:
                    merge_metadata_all[col_name] = merge_meta

            # Calculate overall confidence from individual merges
            if confidences:
                domain_row["avg_confidence"] = sum(confidences) / len(confidences)

            if include_merge_metadata:
                domain_row["_merge_metadata"] = merge_metadata_all

            domain_rows.append(domain_row)

        # Update columns for domain output (remove source-specific columns)
        domain_columns = ["domain"] + [
            c for c in columns
            if c not in ("source_url", "source_title", "domain")
        ]

        # Update labels
        domain_labels = {"domain": "Domain"}
        domain_labels.update(labels)

        return domain_rows, domain_columns, domain_labels

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
                    # Sanitize each list item for markdown table compatibility
                    sanitized_items = [
                        str(v).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
                        for v in val
                    ]
                    values.append(", ".join(sanitized_items))
                else:
                    # Sanitize for markdown table: newlines and pipe characters
                    values.append(
                        str(val).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
                    )
            lines.append("| " + " | ".join(values) + " |")

        return "\n".join(lines)

    async def _generate_table_report(
        self,
        data: ReportData,
        title: str | None,
        columns: list[str] | None,
        output_format: str,
        project_id: UUID | None = None,
        group_by: str = "source",
        include_merge_metadata: bool = False,
    ) -> tuple[str, bytes | None]:
        """Generate table report in markdown or Excel.

        Args:
            data: Aggregated report data.
            title: Report title.
            columns: Fields to include as columns (None = all from schema).
            output_format: Output format ("md" or "xlsx").
            project_id: Project ID for schema-driven columns/labels.
            group_by: "source" (one row per URL) or "domain" (LLM merged per domain).
            include_merge_metadata: Include merge provenance for domain grouping.

        Returns:
            Tuple of (markdown_content, excel_bytes or None).
        """
        # Load schema - required for new aggregation
        extraction_schema = None
        if project_id:
            extraction_schema = self._get_project_schema(project_id)

        if not extraction_schema:
            logger.warning(
                "table_report_no_schema",
                project_id=str(project_id) if project_id else None,
            )
            # Return empty table
            return "# No extraction schema found\n\nCannot generate table.", None

        # Aggregate based on group_by
        if group_by == "domain":
            rows, final_columns, labels = await self._aggregate_by_domain(
                data, extraction_schema, include_merge_metadata
            )
        else:  # "source" (default)
            rows, final_columns, labels, _ = self._aggregate_by_source(
                data, extraction_schema
            )

        # Filter columns if specified
        if columns:
            final_columns = [c for c in final_columns if c in columns or c in ("source_url", "source_title", "domain", "avg_confidence")]

        md_content = self._build_markdown_table(rows, final_columns, title, labels)

        if output_format == "xlsx":
            formatter = ExcelFormatter()
            excel_bytes = formatter.create_workbook(
                rows=rows,
                columns=final_columns,
                column_labels=labels,
                sheet_name=title or "Report",
            )
            return md_content, excel_bytes

        return md_content, None
