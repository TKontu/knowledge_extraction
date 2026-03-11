"""Consolidated table report builder.

Reads pre-computed ConsolidatedExtraction records and produces
multi-sheet or single-sheet table data. Zero LLM calls.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from services.reports.schema_table_generator import SchemaTableGenerator


@dataclass
class SheetData:
    """One worksheet of tabular data."""

    name: str  # Display name (humanized, for Excel tabs / markdown headers)
    rows: list[dict[str, Any]]
    columns: list[str]
    labels: dict[str, str]
    column_types: dict[str, str] = field(default_factory=dict)
    key: str = ""  # Raw schema identifier for lookups (e.g., "products_gearbox")


@dataclass
class ConsolidatedReportData:
    """All sheets for a consolidated report."""

    company_sheet: SheetData
    entity_sheets: list[SheetData]
    list_expansion_sheets: list[SheetData]
    summary: dict[str, Any]


class ConsolidatedReportBuilder:
    """Builds consolidated report data from ConsolidatedExtraction records."""

    def __init__(self, schema_generator: SchemaTableGenerator) -> None:
        self._gen = schema_generator

    def gather(
        self,
        records: list,
        schema: dict,
        include_provenance: bool = False,
    ) -> ConsolidatedReportData:
        """Build report data from consolidated extraction records.

        Args:
            records: List of ConsolidatedExtraction ORM objects.
            schema: Project extraction_schema dict.
            include_provenance: Whether to add provenance columns.

        Returns:
            ConsolidatedReportData with company + entity sheets.
        """
        scalar_columns, scalar_labels, scalar_types = self._gen.get_scalar_columns(schema)
        entity_groups = self._gen.get_entity_list_groups(schema)

        # Group records by source_group
        by_source_group: dict[str, dict[str, Any]] = {}
        for rec in records:
            sg = rec.source_group
            if sg not in by_source_group:
                by_source_group[sg] = {}
            by_source_group[sg][rec.extraction_type] = rec

        # Build company rows (scalar fields)
        company_rows = []
        for sg in sorted(by_source_group.keys()):
            recs_by_type = by_source_group[sg]
            row = self._build_company_row(
                sg, recs_by_type, scalar_columns, scalar_types, include_provenance
            )
            company_rows.append(row)

        # Provenance columns
        company_cols = list(scalar_columns)
        company_labels = dict(scalar_labels)
        if include_provenance:
            for pc, pl in [
                ("source_count", "Sources"),
                ("avg_agreement", "Avg Agreement"),
                ("grounded_pct", "Grounded %"),
            ]:
                company_cols.append(pc)
                company_labels[pc] = pl

        company_sheet = SheetData(
            name="Companies",
            rows=company_rows,
            columns=company_cols,
            labels=company_labels,
            column_types=scalar_types,
        )

        # Build entity sheets
        entity_sheets = []
        for group_name in entity_groups:
            entity_cols, entity_labels, entity_types = self._gen.get_entity_group_columns(
                schema, group_name
            )
            all_rows = []
            for sg in sorted(by_source_group.keys()):
                rec = by_source_group[sg].get(group_name)
                if rec:
                    rows = self._build_entity_rows(sg, rec, entity_cols)
                    all_rows.extend(rows)

            entity_sheets.append(SheetData(
                name=self._gen._humanize(group_name),
                rows=all_rows,
                columns=entity_cols,
                labels=entity_labels,
                column_types=entity_types,
                key=group_name,
            ))

        # Build list expansion sheets (list-of-dict fields in scalar groups)
        list_expansion_sheets = self._build_list_expansion_sheets(
            by_source_group, schema, entity_groups
        )

        # Summary
        summary = {
            "total_companies": len(by_source_group),
            "entity_counts": {
                s.name: len(s.rows) for s in entity_sheets
            },
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        return ConsolidatedReportData(
            company_sheet=company_sheet,
            entity_sheets=entity_sheets,
            list_expansion_sheets=list_expansion_sheets,
            summary=summary,
        )

    def _build_company_row(
        self,
        source_group: str,
        records_by_type: dict[str, Any],
        scalar_columns: list[str],
        scalar_types: dict[str, str],
        include_provenance: bool,
    ) -> dict[str, Any]:
        """Build one company row from scalar consolidated records."""
        row: dict[str, Any] = {"source_group": source_group}

        # Collect provenance stats
        all_source_counts = []
        all_agreements = []
        all_grounded = []

        for _ext_type, rec in records_by_type.items():
            data = rec.data or {}
            provenance = rec.provenance or {}

            for col in scalar_columns:
                if col == "source_group":
                    continue
                if col in data and col not in row:
                    value = data[col]
                    # List-of-dicts → summary placeholder
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        row[col] = f"{len(value)} items"
                    # Flat list → comma-separated
                    elif isinstance(value, list):
                        row[col] = ", ".join(str(v) for v in value)
                    else:
                        row[col] = value

            # Provenance tracking
            if include_provenance:
                all_source_counts.append(rec.source_count or 0)
                for _field_name, prov in provenance.items():
                    if isinstance(prov, dict):
                        agreement = prov.get("agreement", 0.0)
                        all_agreements.append(agreement)
                        if prov.get("grounded_count", 0) > 0:
                            all_grounded.append(1)
                        else:
                            all_grounded.append(0)

        if include_provenance:
            row["source_count"] = max(all_source_counts) if all_source_counts else 0
            row["avg_agreement"] = (
                round(sum(all_agreements) / len(all_agreements), 2)
                if all_agreements
                else None
            )
            row["grounded_pct"] = (
                round(sum(all_grounded) / len(all_grounded) * 100, 1)
                if all_grounded
                else None
            )

        return row

    def _build_entity_rows(
        self,
        source_group: str,
        record: Any,
        entity_columns: list[str],
    ) -> list[dict[str, Any]]:
        """Build entity rows from a consolidated entity list record."""
        data = record.data or {}
        # Entity data may be stored under the extraction_type key or directly as a list
        items = data.get(record.extraction_type, [])
        if not isinstance(items, list):
            # Data might be stored flat — try to find any list value
            for v in data.values():
                if isinstance(v, list):
                    items = v
                    break
            else:
                items = []

        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            row: dict[str, Any] = {"source_group": source_group}
            for col in entity_columns:
                if col == "source_group":
                    continue
                row[col] = item.get(col)
            rows.append(row)

        return rows

    def _build_list_expansion_sheets(
        self,
        by_source_group: dict[str, dict[str, Any]],
        schema: dict,
        entity_groups: dict,
    ) -> list[SheetData]:
        """Detect list-of-dict fields in scalar groups and expand to sheets."""
        from services.extraction.schema_adapter import SchemaAdapter

        adapter = SchemaAdapter()
        field_groups = adapter.convert_to_field_groups(schema)

        # Find list-of-dict fields in non-entity groups
        list_fields: dict[str, str] = {}  # field_name → group_name
        for group in field_groups:
            if group.is_entity_list:
                continue
            for fd in group.fields:
                if fd.field_type == "list":
                    list_fields[fd.name] = group.name

        if not list_fields:
            return []

        sheets = []
        for field_name, group_name in list_fields.items():
            all_items: list[tuple[str, list[dict]]] = []
            for sg, recs_by_type in by_source_group.items():
                rec = recs_by_type.get(group_name)
                if not rec:
                    continue
                data = rec.data or {}
                value = data.get(field_name)
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    all_items.append((sg, value))

            if not all_items:
                continue

            sheet = self._expand_list_field(field_name, all_items)
            if sheet.rows:
                sheets.append(sheet)

        return sheets

    def _expand_list_field(
        self,
        field_name: str,
        all_items: list[tuple[str, list[dict]]],
    ) -> SheetData:
        """Expand a list-of-dict field into a flat sheet."""
        # Union all dict keys to derive columns
        all_keys: dict[str, None] = {}  # ordered set
        for _sg, items in all_items:
            for item in items:
                if isinstance(item, dict):
                    for k in item:
                        all_keys[k] = None

        columns = ["source_group"] + list(all_keys.keys())
        labels = {"source_group": "Source"}
        for k in all_keys:
            labels[k] = self._gen._humanize(k)

        rows = []
        for sg, items in all_items:
            for item in items:
                if not isinstance(item, dict):
                    continue
                row: dict[str, Any] = {"source_group": sg}
                for k in all_keys:
                    row[k] = item.get(k)
                rows.append(row)

        return SheetData(
            name=self._gen._humanize(field_name),
            rows=rows,
            columns=columns,
            labels=labels,
        )


def compose_multi_sheet(data: ConsolidatedReportData) -> list[SheetData]:
    """Compose multi-sheet layout: company + entity sheets."""
    sheets = [data.company_sheet]
    sheets.extend(s for s in data.entity_sheets if s.rows)
    sheets.extend(s for s in data.list_expansion_sheets if s.rows)
    return sheets


def compose_single_sheet(
    data: ConsolidatedReportData,
    entity_focus: str | None,
    schema: dict,
) -> SheetData:
    """Compose single-sheet layout.

    Args:
        data: Consolidated report data.
        entity_focus: None (company-only with entity counts),
                      specific group name (denormalized), or "all".
        schema: Extraction schema.

    Returns:
        Single SheetData with combined rows.
    """
    if entity_focus is None:
        # Company rows + entity count summary columns
        columns = list(data.company_sheet.columns)
        labels = dict(data.company_sheet.labels)

        # Add entity count columns
        for es in data.entity_sheets:
            count_col = f"{es.name}_count"
            columns.append(count_col)
            labels[count_col] = f"{es.name} Count"

        # Build company-to-entity-count lookup
        entity_counts_by_sg: dict[str, dict[str, int]] = {}
        for es in data.entity_sheets:
            for row in es.rows:
                sg = row.get("source_group", "")
                if sg not in entity_counts_by_sg:
                    entity_counts_by_sg[sg] = {}
                entity_counts_by_sg[sg][es.name] = (
                    entity_counts_by_sg[sg].get(es.name, 0) + 1
                )

        rows = []
        for company_row in data.company_sheet.rows:
            row = dict(company_row)
            sg = row.get("source_group", "")
            for es in data.entity_sheets:
                count_col = f"{es.name}_count"
                row[count_col] = entity_counts_by_sg.get(sg, {}).get(es.name, 0)
            rows.append(row)

        return SheetData(
            name="Report",
            rows=rows,
            columns=columns,
            labels=labels,
        )

    if entity_focus == "all":
        # Superset: company cols + all entity cols + entity_type discriminator
        columns = list(data.company_sheet.columns) + ["entity_type"]
        labels = dict(data.company_sheet.labels)
        labels["entity_type"] = "Entity Type"

        # Collect all entity columns
        all_entity_cols: dict[str, str] = {}  # col → label
        for es in data.entity_sheets:
            for col in es.columns:
                if col != "source_group" and col not in all_entity_cols:
                    all_entity_cols[col] = es.labels.get(col, col)
        columns.extend(all_entity_cols.keys())
        labels.update(all_entity_cols)

        rows = []
        company_lookup = {
            r["source_group"]: r for r in data.company_sheet.rows
        }
        for es in data.entity_sheets:
            for entity_row in es.rows:
                sg = entity_row.get("source_group", "")
                row = dict(company_lookup.get(sg, {"source_group": sg}))
                row["entity_type"] = es.name
                for col in all_entity_cols:
                    row[col] = entity_row.get(col)
                rows.append(row)

        return SheetData(name="Report", rows=rows, columns=columns, labels=labels)

    # Specific entity focus — denormalized (match by key or display name)
    target_sheet = _find_entity_sheet(data.entity_sheets, entity_focus)
    if target_sheet is None:
        available = [es.key or es.name for es in data.entity_sheets]
        raise ValueError(
            f"Unknown entity group '{entity_focus}'. "
            f"Available: {available}"
        )

    columns = list(data.company_sheet.columns)
    labels = dict(data.company_sheet.labels)
    entity_cols = [c for c in target_sheet.columns if c != "source_group"]
    columns.extend(entity_cols)
    for c in entity_cols:
        labels[c] = target_sheet.labels.get(c, c)

    rows = []
    company_lookup = {r["source_group"]: r for r in data.company_sheet.rows}

    # Group entity rows by source_group
    entity_by_sg: dict[str, list[dict]] = {}
    for er in target_sheet.rows:
        sg = er.get("source_group", "")
        entity_by_sg.setdefault(sg, []).append(er)

    for sg in sorted(entity_by_sg.keys()):
        sg_entities = entity_by_sg[sg]
        for i, entity_row in enumerate(sg_entities):
            row: dict[str, Any] = {}
            if i == 0:
                # First row gets company data
                row.update(company_lookup.get(sg, {"source_group": sg}))
            else:
                # Subsequent rows: blank company cols, keep source_group
                row["source_group"] = sg
            for c in entity_cols:
                row[c] = entity_row.get(c)
            rows.append(row)

    return SheetData(name="Report", rows=rows, columns=columns, labels=labels)


def _find_entity_sheet(
    entity_sheets: list[SheetData], entity_focus: str
) -> SheetData | None:
    """Find an entity sheet by key (raw name) or display name.

    Args:
        entity_sheets: List of entity SheetData objects.
        entity_focus: Raw group name or humanized display name.

    Returns:
        Matching SheetData, or None if not found.
    """
    for es in entity_sheets:
        if es.key == entity_focus or es.name == entity_focus:
            return es
    return None


def validate_entity_focus(entity_focus: str, schema: dict) -> None:
    """Validate entity_focus against schema's entity list groups.

    Accepts raw schema names (e.g., "products_gearbox") or humanized
    display names (e.g., "Products Gearbox").

    Args:
        entity_focus: Entity group name to validate.
        schema: Extraction schema.

    Raises:
        ValueError: If entity_focus is not a valid entity list group.
    """
    if entity_focus == "all":
        return

    gen = SchemaTableGenerator()
    entity_groups = gen.get_entity_list_groups(schema)
    valid_names = set(entity_groups.keys())
    humanized = {gen._humanize(name) for name in valid_names}
    if entity_focus not in valid_names and entity_focus not in humanized:
        raise ValueError(
            f"Unknown entity group '{entity_focus}'. "
            f"Available: {sorted(valid_names)}"
        )


def render_markdown(
    sheets: list[SheetData],
    summary: dict[str, Any],
) -> str:
    """Render sheets as markdown with summary header.

    Args:
        sheets: List of SheetData to render.
        summary: Summary dict for the header.

    Returns:
        Markdown string.
    """
    lines = [
        f"Generated: {summary.get('generated_at', '')}",
        f"Companies: {summary.get('total_companies', 0)}",
    ]

    entity_counts = summary.get("entity_counts", {})
    if entity_counts:
        parts = [f"{name}: {count}" for name, count in entity_counts.items() if count > 0]
        if parts:
            lines.append(f"Entities: {', '.join(parts)}")

    lines.append("")

    for sheet in sheets:
        lines.append(f"## {sheet.name}")
        lines.append("")

        if not sheet.rows:
            lines.append("*No data*")
            lines.append("")
            continue

        # Header
        header_labels = [sheet.labels.get(c, c) for c in sheet.columns]
        # Sanitize labels
        header_labels = [_sanitize_md(label) for label in header_labels]
        lines.append("| " + " | ".join(header_labels) + " |")
        lines.append("|" + "|".join(["---"] * len(sheet.columns)) + "|")

        # Rows
        for row in sheet.rows:
            values = []
            for col in sheet.columns:
                val = row.get(col)
                values.append(_format_md_value(val))
            lines.append("| " + " | ".join(values) + " |")

        lines.append("")

    return "\n".join(lines)


def _sanitize_md(text: str) -> str:
    """Sanitize text for markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _format_md_value(val: Any) -> str:
    """Format a value for markdown table cell."""
    if val is None:
        return "N/A"
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, list):
        return _sanitize_md(", ".join(str(v) for v in val))
    return _sanitize_md(str(val))
