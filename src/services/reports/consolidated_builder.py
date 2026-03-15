"""Consolidated table report builder.

Reads pre-computed ConsolidatedExtraction records and produces
a unified 3-sheet report (Data, Quality, Sources). Zero LLM calls.

Entity list columns are paginated horizontally: if a source_group has
more entities than page_size, additional page columns are added to the
right (e.g., "Products Gearbox (1-50)", "Products Gearbox (51-100)").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from typing import Any

from services.extraction.field_groups import FieldGroup
from services.reports.schema_table_generator import SchemaTableGenerator

# Minimum entity winning_weight to include in report
ENTITY_MIN_QUALITY = 0.3

# Default page size for entity list horizontal pagination
ENTITY_PAGE_SIZE = 50


@dataclass
class SheetData:
    """One worksheet of tabular data."""

    name: str  # Display name (humanized, for Excel tabs / markdown headers)
    rows: list[dict[str, Any]]
    columns: list[str]
    labels: dict[str, str]
    column_types: dict[str, str] = field(default_factory=dict)
    key: str = ""  # Raw schema identifier for lookups (e.g., "products_gearbox")

    # Maps paginated entity column → provenance key (e.g., "products_gearbox").
    # Used by build_provenance_sheets() instead of suffix stripping.
    provenance_key_map: dict[str, str] = field(default_factory=dict)

    # Per-row, per-column entity provenance for quality computation.
    # row_entity_provenance[row_idx][col] = list of per-entity prov dicts.
    row_entity_provenance: list[dict[str, list[dict]]] = field(default_factory=list)


@dataclass
class _EntityRaw:
    """Raw entity data for one cell, pre-formatting."""

    items: list[dict]
    provenance: list[dict]
    group: FieldGroup


class ConsolidatedReportBuilder:
    """Builds unified consolidated report data from ConsolidatedExtraction records."""

    def __init__(self, schema_generator: SchemaTableGenerator) -> None:
        self._gen = schema_generator

    def gather(
        self,
        records: list,
        schema: dict,
        page_size: int = ENTITY_PAGE_SIZE,
    ) -> tuple[SheetData, dict[str, Any]]:
        """Build unified data sheet from consolidated extraction records.

        One row per source_group with ALL fields: scalars inline,
        entity lists formatted into paginated cells.

        Args:
            records: List of ConsolidatedExtraction ORM objects.
            schema: Project extraction_schema dict.
            page_size: Max entities per cell before paginating to next column.

        Returns:
            Tuple of (data_sheet, summary).
        """
        columns, labels, col_types, entity_groups = self._gen.get_unified_columns(
            schema
        )

        # Get source_label for sheet naming
        context = schema.get("extraction_context", {})
        source_label = context.get("source_label", "Source")

        # Group records by source_group
        by_source_group: dict[str, dict[str, Any]] = {}
        for rec in records:
            sg = rec.source_group
            if sg not in by_source_group:
                by_source_group[sg] = {}
            by_source_group[sg][rec.extraction_type] = rec

        # Phase 1: Build rows with raw entity data (unformatted)
        rows: list[dict[str, Any]] = []
        raw_entity_data: list[dict[str, _EntityRaw]] = []
        for sg in sorted(by_source_group.keys()):
            recs_by_type = by_source_group[sg]
            row, entity_raw = self._build_unified_row(
                sg, recs_by_type, columns, col_types, entity_groups
            )
            rows.append(row)
            raw_entity_data.append(entity_raw)

        # Phase 2: Paginate entity columns and format cells
        columns, labels, col_types, prov_key_map, row_entity_prov = (
            self._paginate_entities(
                rows,
                raw_entity_data,
                columns,
                labels,
                col_types,
                entity_groups,
                page_size,
            )
        )

        # Count entities per entity group (from raw DB data, not display)
        entity_counts: dict[str, int] = {}
        for _group_col, group in entity_groups.items():
            total = 0
            for _sg, recs_by_type in by_source_group.items():
                rec = recs_by_type.get(group.name)
                if rec:
                    items = (rec.data or {}).get(group.name, [])
                    if isinstance(items, list):
                        total += len(items)
            entity_counts[self._gen._humanize(group.name)] = total

        data_sheet = SheetData(
            name=f"{source_label} Data",
            rows=rows,
            columns=columns,
            labels=labels,
            column_types=col_types,
            provenance_key_map=prov_key_map,
            row_entity_provenance=row_entity_prov,
        )

        summary = {
            "total_count": len(by_source_group),
            "entity_counts": entity_counts,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        return data_sheet, summary

    def _build_unified_row(
        self,
        source_group: str,
        records_by_type: dict[str, Any],
        columns: list[str],
        col_types: dict[str, str],
        entity_groups: dict[str, FieldGroup],
    ) -> tuple[dict[str, Any], dict[str, _EntityRaw]]:
        """Build one unified row from all consolidated records for a source_group.

        Returns:
            Tuple of (row_dict, entity_raw_dict).
            Entity list columns get placeholder "N/A" in the row; actual items
            are in entity_raw for pagination in phase 2.
        """
        row: dict[str, Any] = {"source_group": source_group}
        entity_raw: dict[str, _EntityRaw] = {}

        for col in columns:
            if col == "source_group":
                continue

            if col_types.get(col) == "entity_list":
                group = entity_groups[col]
                rec = records_by_type.get(group.name)
                if rec:
                    items = (rec.data or {}).get(group.name, [])
                    prov = (rec.provenance or {}).get(group.name, {})
                    entity_prov = (
                        prov.get("entity_provenance")
                        if isinstance(prov, dict)
                        else None
                    )
                    if isinstance(items, list) and items:
                        # Quality-filter entities
                        filtered_items, filtered_prov = _filter_entities(
                            items,
                            entity_prov,
                        )
                        if filtered_items:
                            entity_raw[col] = _EntityRaw(
                                items=filtered_items,
                                provenance=filtered_prov,
                                group=group,
                            )
                            row[col] = None  # placeholder for phase 2
                            continue
                row[col] = "N/A"
            else:
                # Scalar column → look across extraction types
                for _ext_type, rec in records_by_type.items():
                    data = rec.data or {}
                    if col in data and col not in row:
                        value = data[col]
                        if (
                            isinstance(value, list)
                            and value
                            and isinstance(value[0], dict)
                        ):
                            row[col] = _format_dict_list(value)
                        elif isinstance(value, list):
                            row[col] = ", ".join(str(v) for v in value)
                        else:
                            row[col] = value

        return row, entity_raw

    def _paginate_entities(
        self,
        rows: list[dict[str, Any]],
        raw_entity_data: list[dict[str, _EntityRaw]],
        columns: list[str],
        labels: dict[str, str],
        col_types: dict[str, str],
        entity_groups: dict[str, FieldGroup],
        page_size: int,
    ) -> tuple[
        list[str],
        dict[str, str],
        dict[str, str],
        dict[str, str],
        list[dict[str, list[dict]]],
    ]:
        """Paginate entity columns and format cells.

        For each entity list column, determines max entity count across
        all rows. If max > page_size, creates additional page columns.
        Formats entity items into cells and slices provenance per page.

        Returns:
            (columns, labels, col_types, provenance_key_map, row_entity_provenance)
        """
        entity_cols = [c for c in columns if col_types.get(c) == "entity_list"]

        # Find max entity count per column across all rows
        max_counts: dict[str, int] = {}
        for col in entity_cols:
            max_count = 0
            for entity_raw in raw_entity_data:
                if col in entity_raw:
                    max_count = max(max_count, len(entity_raw[col].items))
            max_counts[col] = max_count

        # Build expanded column list
        new_columns: list[str] = []
        new_labels: dict[str, str] = {}
        new_col_types: dict[str, str] = {}
        prov_key_map: dict[str, str] = {}

        for col in columns:
            if col not in entity_cols:
                new_columns.append(col)
                if col in labels:
                    new_labels[col] = labels[col]
                if col in col_types:
                    new_col_types[col] = col_types[col]
            else:
                max_count = max_counts.get(col, 0)
                num_pages = max(1, ceil(max_count / page_size)) if max_count > 0 else 1
                group = entity_groups[col]
                base_label = labels.get(col, self._gen._humanize(group.name))
                prov_key = group.name

                for page in range(num_pages):
                    page_col = col if page == 0 else f"{col}_p{page + 1}"
                    if num_pages == 1:
                        page_label = base_label
                    else:
                        start = page * page_size + 1
                        end = min((page + 1) * page_size, max_count)
                        page_label = f"{base_label} ({start}-{end})"

                    new_columns.append(page_col)
                    new_labels[page_col] = page_label
                    new_col_types[page_col] = "entity_list"
                    prov_key_map[page_col] = prov_key

        # Format cells and build per-row entity provenance
        row_entity_prov: list[dict[str, list[dict]]] = []
        for row, entity_raw in zip(rows, raw_entity_data, strict=True):
            row_prov: dict[str, list[dict]] = {}

            for col in entity_cols:
                max_count = max_counts.get(col, 0)
                num_pages = max(1, ceil(max_count / page_size)) if max_count > 0 else 1

                if col not in entity_raw:
                    # No data — fill all page columns with N/A
                    for page in range(num_pages):
                        page_col = col if page == 0 else f"{col}_p{page + 1}"
                        row[page_col] = "N/A"
                    continue

                raw = entity_raw[col]
                for page in range(num_pages):
                    page_col = col if page == 0 else f"{col}_p{page + 1}"
                    start = page * page_size
                    end = (page + 1) * page_size

                    page_items = raw.items[start:end]
                    page_prov = (
                        raw.provenance[start:end]
                        if raw.provenance and start < len(raw.provenance)
                        else None
                    )

                    if page_items:
                        row[page_col] = self._gen.format_entity_list(
                            page_items,
                            raw.group,
                            max_items=page_size,
                        )
                        if page_prov:
                            row_prov[page_col] = page_prov
                    else:
                        row[page_col] = "N/A"

            row_entity_prov.append(row_prov)

        return new_columns, new_labels, new_col_types, prov_key_map, row_entity_prov


def _filter_entities(
    items: list[dict],
    entity_provenance: list[dict] | None,
) -> tuple[list[dict], list[dict]]:
    """Filter entities by quality threshold.

    Returns:
        Tuple of (filtered_items, filtered_provenance).
    """
    if entity_provenance and len(entity_provenance) == len(items):
        filtered_items = []
        filtered_prov = []
        for item, prov in zip(items, entity_provenance, strict=True):
            if prov.get("winning_weight", 0) >= ENTITY_MIN_QUALITY:
                filtered_items.append(item)
                filtered_prov.append(prov)
        return filtered_items, filtered_prov
    return list(items), list(entity_provenance or [])


def build_provenance_sheets(
    data_sheet: SheetData,
    records_by_sg: dict[str, dict[str, Any]],
    source_url_map: dict[str, str],
) -> tuple[SheetData, SheetData]:
    """Build Quality and Sources companion sheets for a data sheet.

    Each cell in the quality sheet shows the winning_weight for the
    corresponding data cell. Each cell in the sources sheet shows
    the source URLs that contributed to that data cell.

    For entity list columns (including paginated pages), uses
    per-cell entity provenance when available, falling back to
    record-level provenance via provenance_key_map or _list suffix
    stripping.

    Args:
        data_sheet: The data sheet to generate companions for.
        records_by_sg: source_group → {extraction_type → ConsolidatedExtraction}.
        source_url_map: source_id → URL mapping.

    Returns:
        Tuple of (quality_sheet, sources_sheet).
    """
    quality_rows: list[dict[str, Any]] = []
    sources_rows: list[dict[str, Any]] = []

    has_entity_prov = bool(data_sheet.row_entity_provenance)

    for row_idx, row in enumerate(data_sheet.rows):
        sg = row.get("source_group", "")
        sg_records = records_by_sg.get(sg, {})

        quality_row: dict[str, Any] = {"source_group": sg}
        sources_row: dict[str, Any] = {"source_group": sg}

        for col in data_sheet.columns:
            if col == "source_group":
                continue

            # Check for per-cell entity provenance (paginated entity columns)
            if has_entity_prov and row_idx < len(data_sheet.row_entity_provenance):
                cell_prov = data_sheet.row_entity_provenance[row_idx].get(col)
                if cell_prov:
                    weights = [ep.get("winning_weight", 0) for ep in cell_prov]
                    quality_row[col] = (
                        round(sum(weights) / len(weights), 4) if weights else "N/A"
                    )
                    # Sources: look up via provenance_key_map
                    prov_key = data_sheet.provenance_key_map.get(col)
                    sources_row[col] = _resolve_entity_sources(
                        prov_key,
                        sg_records,
                        source_url_map,
                    )
                    continue

            # Scalar fields: look up provenance directly
            prov = _find_field_provenance(col, sg_records)

            if prov:
                # Check for entity_provenance (non-paginated entity column)
                entity_prov = prov.get("entity_provenance")
                if entity_prov and isinstance(entity_prov, list):
                    weights = [
                        ep.get("winning_weight", 0)
                        for ep in entity_prov
                        if ep.get("winning_weight", 0) >= ENTITY_MIN_QUALITY
                    ]
                    quality_row[col] = (
                        round(sum(weights) / len(weights), 4) if weights else "N/A"
                    )
                else:
                    ww = prov.get("winning_weight")
                    quality_row[col] = round(ww, 4) if ww is not None else "N/A"

                top_src_ids = prov.get("top_sources", [])
                urls = [
                    source_url_map.get(str(sid), str(sid))
                    for sid in top_src_ids
                    if str(sid) in source_url_map
                ]
                sources_row[col] = "\n".join(urls) if urls else "N/A"
            else:
                quality_row[col] = "N/A"
                sources_row[col] = "N/A"

        quality_rows.append(quality_row)
        sources_rows.append(sources_row)

    quality_sheet = SheetData(
        name=f"{data_sheet.name} - Quality",
        rows=quality_rows,
        columns=list(data_sheet.columns),
        labels=dict(data_sheet.labels),
    )

    sources_sheet = SheetData(
        name=f"{data_sheet.name} - Sources",
        rows=sources_rows,
        columns=list(data_sheet.columns),
        labels=dict(data_sheet.labels),
    )

    return quality_sheet, sources_sheet


def _find_field_provenance(
    col: str,
    sg_records: dict[str, Any],
) -> dict | None:
    """Find provenance for a column across extraction types.

    Tries direct match, then _list suffix stripping for entity columns.
    """
    for _ext_type, rec in sg_records.items():
        rec_provenance = rec.provenance or {}
        if col in rec_provenance and isinstance(rec_provenance[col], dict):
            return rec_provenance[col]

    # Fallback: strip _list suffix for entity list columns
    stripped = col.removesuffix("_list")
    if stripped != col:
        for _ext_type, rec in sg_records.items():
            rec_provenance = rec.provenance or {}
            if stripped in rec_provenance and isinstance(
                rec_provenance[stripped], dict
            ):
                return rec_provenance[stripped]

    return None


def _resolve_entity_sources(
    prov_key: str | None,
    sg_records: dict[str, Any],
    source_url_map: dict[str, str],
) -> str:
    """Resolve source URLs for an entity column via its provenance key."""
    if not prov_key:
        return "N/A"
    for _ext_type, rec in sg_records.items():
        rec_provenance = rec.provenance or {}
        if prov_key in rec_provenance and isinstance(rec_provenance[prov_key], dict):
            top_src_ids = rec_provenance[prov_key].get("top_sources", [])
            urls = [
                source_url_map.get(str(sid), str(sid))
                for sid in top_src_ids
                if str(sid) in source_url_map
            ]
            return "\n".join(urls) if urls else "N/A"
    return "N/A"


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
        f"Total: {summary.get('total_count', 0)}",
    ]

    entity_counts = summary.get("entity_counts", {})
    if entity_counts:
        parts = [
            f"{name}: {count}" for name, count in entity_counts.items() if count > 0
        ]
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


def _format_dict_list(items: list[dict], max_items: int = 50) -> str:
    """Format a list of dicts as newline-separated semicolon-delimited rows.

    Args:
        items: List of dicts to format.
        max_items: Maximum items to include.

    Returns:
        Formatted string, or "N/A" if empty.
    """
    if not items:
        return "N/A"

    parts = []
    for item in items[:max_items]:
        values = [str(v) for v in item.values() if v is not None]
        parts.append(" ; ".join(values) if values else "N/A")

    result = "\n".join(parts)
    if len(items) > max_items:
        result += f"\n(+{len(items) - max_items} more)"
    return result


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
