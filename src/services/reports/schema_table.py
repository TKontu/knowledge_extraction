"""Generate tabular reports from schema extractions."""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from orm_models import Extraction
from services.extraction.field_groups import (
    FIELD_GROUPS_BY_NAME,
)
from services.reports.excel_formatter import ExcelFormatter


class SchemaTableReport:
    """Generates tabular reports from schema extractions."""

    def __init__(self, db_session: Session):
        self._db = db_session

    async def generate(
        self,
        project_id: UUID,
        source_groups: list[str],
        output_format: str = "md",
    ) -> tuple[str, bytes | None]:
        """Generate table report with one row per company.

        Args:
            project_id: Project UUID.
            source_groups: Companies to include.
            output_format: "md" or "xlsx".

        Returns:
            Tuple of (markdown_content, excel_bytes or None).
        """
        # Gather all extractions for these companies
        extractions = (
            self._db.query(Extraction)
            .filter(
                Extraction.project_id == project_id,
                Extraction.source_group.in_(source_groups),
            )
            .all()
        )

        # Aggregate per company
        rows = []
        for company in source_groups:
            company_extractions = [e for e in extractions if e.source_group == company]
            row = self._aggregate_company(company, company_extractions)
            rows.append(row)

        # Build columns list
        columns = self._get_column_order()

        # Generate output
        if output_format == "xlsx":
            formatter = ExcelFormatter()
            excel_bytes = formatter.create_workbook(
                rows=rows,
                columns=columns,
                column_labels=self._get_column_labels(),
                sheet_name="Company Comparison",
            )
            md_content = self._build_markdown_table(rows, columns)
            return md_content, excel_bytes

        return self._build_markdown_table(rows, columns), None

    def _aggregate_company(
        self, company: str, extractions: list[Extraction]
    ) -> dict[str, Any]:
        """Aggregate all extractions for a company into one row."""
        row = {"company": company}

        # Group extractions by type
        by_type: dict[str, list[dict]] = {}
        for ext in extractions:
            ext_type = ext.extraction_type
            if ext_type not in by_type:
                by_type[ext_type] = []
            by_type[ext_type].append(ext.data)

        # Aggregate each field group
        for group_name, data_list in by_type.items():
            group = FIELD_GROUPS_BY_NAME.get(group_name)
            if not group:
                continue

            if group.is_entity_list:
                # Products - merge all
                all_products = []
                for data in data_list:
                    all_products.extend(data.get("products", []))
                row[f"{group_name}_list"] = self._format_product_list(all_products)
            else:
                # Regular fields - merge
                merged = self._merge_field_group_data(data_list, group)
                row.update(merged)

        return row

    def _merge_field_group_data(self, data_list: list[dict], group) -> dict[str, Any]:
        """Merge multiple extraction data dicts for a field group."""
        merged = {}

        for field in group.fields:
            values = [
                d.get(field.name) for d in data_list if d.get(field.name) is not None
            ]

            if not values:
                merged[field.name] = None
                continue

            if field.field_type == "boolean":
                merged[field.name] = any(values)
            elif field.field_type in ("integer", "float"):
                merged[field.name] = max(values)
            elif field.field_type == "list":
                flat = []
                for v in values:
                    if isinstance(v, list):
                        flat.extend(v)
                    else:
                        flat.append(v)
                # Dedupe - handle both hashable (strings) and unhashable (dicts)
                if flat and isinstance(flat[0], dict):
                    import json
                    seen = set()
                    unique = []
                    for item in flat:
                        key = json.dumps(item, sort_keys=True)
                        if key not in seen:
                            seen.add(key)
                            unique.append(item)
                    merged[field.name] = unique
                else:
                    merged[field.name] = list(dict.fromkeys(flat))
            else:
                # Text - concatenate unique non-empty values
                unique = list(dict.fromkeys([str(v) for v in values if v]))
                merged[field.name] = (
                    "; ".join(unique)
                    if len(unique) > 1
                    else (unique[0] if unique else None)
                )

        return merged

    def _format_product_list(self, products: list[dict]) -> str:
        """Format product list for table cell."""
        if not products:
            return "N/A"

        parts = []
        for p in products[:10]:  # Limit to 10
            name = p.get("product_name", "Unknown")
            specs = []
            if p.get("power_rating_kw"):
                specs.append(f"{p['power_rating_kw']}kW")
            if p.get("torque_rating_nm"):
                specs.append(f"{p['torque_rating_nm']}Nm")
            if p.get("ratio"):
                specs.append(p["ratio"])

            if specs:
                parts.append(f"{name} ({', '.join(specs)})")
            else:
                parts.append(name)

        return "; ".join(parts)

    def _get_column_order(self) -> list[str]:
        """Get ordered list of columns for table."""
        return [
            "company",
            # Manufacturing
            "manufactures_gearboxes",
            "manufactures_motors",
            "manufactures_drivetrain_accessories",
            "manufacturing_details",
            # Services
            "provides_services",
            "services_gearboxes",
            "services_motors",
            "provides_field_service",
            "service_types",
            # Company
            "company_name",
            "employee_count",
            "headquarters_location",
            "number_of_sites",
            # Products
            "products_gearbox_list",
            "products_motor_list",
            "products_accessory_list",
            # Meta
            "certifications",
            "locations",
        ]

    def _get_column_labels(self) -> dict[str, str]:
        """Get human-readable column labels."""
        return {
            "company": "Company",
            "manufactures_gearboxes": "Mfg Gearboxes",
            "manufactures_motors": "Mfg Motors",
            "manufactures_drivetrain_accessories": "Mfg Accessories",
            "manufacturing_details": "Mfg Details",
            "provides_services": "Services",
            "services_gearboxes": "Svc Gearboxes",
            "services_motors": "Svc Motors",
            "provides_field_service": "Field Service",
            "service_types": "Service Types",
            "company_name": "Legal Name",
            "employee_count": "Employees",
            "headquarters_location": "HQ Location",
            "number_of_sites": "Sites",
            "products_gearbox_list": "Gearbox Products",
            "products_motor_list": "Motor Products",
            "products_accessory_list": "Accessory Products",
            "certifications": "Certifications",
            "locations": "Locations",
        }

    def _build_markdown_table(self, rows: list[dict], columns: list[str]) -> str:
        """Build markdown table."""
        labels = self._get_column_labels()
        lines = []

        # Header
        header_cells = [labels.get(c, c) for c in columns]
        lines.append("| " + " | ".join(header_cells) + " |")
        lines.append("|" + "|".join(["---"] * len(columns)) + "|")

        # Data rows
        for row in rows:
            cells = []
            for col in columns:
                val = row.get(col)
                if val is None:
                    cells.append("N/A")
                elif isinstance(val, bool):
                    cells.append("Yes" if val else "No")
                elif isinstance(val, list):
                    cells.append("; ".join(str(v) for v in val))
                else:
                    cells.append(str(val)[:50])  # Truncate for MD
            lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)
