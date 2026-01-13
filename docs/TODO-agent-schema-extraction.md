# Agent Task: Schema-Based Multi-Pass Extraction Pipeline

**Agent ID:** `agent-schema-extraction`
**Branch:** `feat/schema-based-extraction`
**Priority:** High

## Objective

Implement a schema-based extraction system that runs multiple focused LLM calls per source document, extracts structured fields (booleans, text, lists, entities), stores results in JSONB, and generates tabular reports with one column per field.

## Context

The current extraction system uses a generic "extract facts" prompt that produces simple `{category, fact_text}` pairs. We need structured extraction that:

1. Uses field-group-specific prompts (manufacturing, services, products, etc.)
2. Extracts typed values (boolean, integer, text, list)
3. Stores structured data in `extractions.data` JSONB
4. Aggregates multi-valued fields across pages per company
5. Generates tables with one column per schema field

**No database changes required** - we use existing JSONB columns.

## Architecture

```
Source (markdown)
       │
       ▼
┌──────────────────────────────────────────────────────┐
│              SchemaExtractor                          │
│  ┌─────────────┬─────────────┬─────────────────────┐ │
│  │ Group 1     │ Group 2     │ Group 3...7         │ │
│  │ Manufacturing│ Services   │ Products, etc.      │ │
│  └─────────────┴─────────────┴─────────────────────┘ │
│         │             │              │               │
│         ▼             ▼              ▼               │
│     LLM Call      LLM Call       LLM Call            │
│         │             │              │               │
│         ▼             ▼              ▼               │
│  extraction_type: extraction_type: extraction_type: │
│  "manufacturing"  "services"    "products_gearbox"  │
└──────────────────────────────────────────────────────┘
       │
       ▼
extractions table (data JSONB)
       │
       ▼
SchemaTableReport (aggregates per company)
       │
       ▼
Excel/Markdown table (1 row per company, 1 col per field)
```

## Field Groups (7 total)

### Group 1: Manufacturing Capabilities
**extraction_type:** `"manufacturing"`
```json
{
  "manufactures_gearboxes": true,
  "manufactures_motors": false,
  "manufactures_drivetrain_accessories": true,
  "manufacturing_details": "Specializes in planetary and helical gearboxes for industrial applications"
}
```

### Group 2: Service Capabilities
**extraction_type:** `"services"`
```json
{
  "provides_services": true,
  "services_gearboxes": true,
  "services_motors": false,
  "services_drivetrain_accessories": false,
  "service_types": ["repair", "maintenance", "refurbishment"]
}
```

### Group 3: Company Information
**extraction_type:** `"company_info"`
```json
{
  "company_name": "Brevini Power Transmission",
  "employee_count": 500,
  "employee_count_range": "501-1000",
  "number_of_sites": 5,
  "headquarters_location": "Reggio Emilia, Italy"
}
```

### Group 4: Gearbox Products
**extraction_type:** `"products_gearbox"`
```json
{
  "products": [
    {
      "product_name": "D Series",
      "series_name": "D",
      "power_rating_kw": 100,
      "torque_rating_nm": 5000,
      "ratio": "1:50",
      "subcategory": "planetary"
    }
  ]
}
```

### Group 5: Motor Products
**extraction_type:** `"products_motor"`
```json
{
  "products": [
    {
      "product_name": "AC Servo Motor",
      "power_rating_kw": 10,
      "speed_rating_rpm": 3000,
      "subcategory": "servo"
    }
  ]
}
```

### Group 6: Accessory Products
**extraction_type:** `"products_accessory"`
```json
{
  "products": [
    {
      "product_name": "Flexible Coupling FC-100",
      "subcategory": "coupling"
    }
  ]
}
```

### Group 7: Certifications & Locations
**extraction_type:** `"company_meta"`
```json
{
  "certifications": ["ISO 9001:2015", "ISO 14001", "ATEX"],
  "locations": [
    {"city": "Reggio Emilia", "country": "Italy", "site_type": "headquarters"},
    {"city": "Munich", "country": "Germany", "site_type": "manufacturing"}
  ]
}
```

## Tasks

### 1. Create Field Group Definitions

**File:** `src/services/extraction/field_groups.py` (NEW)

```python
"""Field group definitions for schema-based extraction."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldDefinition:
    """Definition of a single extraction field."""
    name: str
    field_type: str  # "boolean", "integer", "text", "list", "float", "enum"
    description: str
    required: bool = False
    default: Any = None
    enum_values: list[str] | None = None


@dataclass
class FieldGroup:
    """Group of related fields for focused extraction."""
    name: str  # Used as extraction_type
    description: str
    fields: list[FieldDefinition]
    prompt_hint: str  # Additional context for LLM
    is_entity_list: bool = False  # True for product groups


# Define all field groups
MANUFACTURING_GROUP = FieldGroup(
    name="manufacturing",
    description="Manufacturing capabilities for drivetrain components",
    fields=[
        FieldDefinition(
            name="manufactures_gearboxes",
            field_type="boolean",
            description="Company manufactures gearboxes/gear reducers/gearheads",
            required=True,
            default=False,
        ),
        FieldDefinition(
            name="manufactures_motors",
            field_type="boolean",
            description="Company manufactures motors (electric, servo, stepper)",
            required=True,
            default=False,
        ),
        FieldDefinition(
            name="manufactures_drivetrain_accessories",
            field_type="boolean",
            description="Company manufactures couplings, shafts, bearings, brakes, clutches",
            required=True,
            default=False,
        ),
        FieldDefinition(
            name="manufacturing_details",
            field_type="text",
            description="Details about manufacturing specializations",
            required=False,
        ),
    ],
    prompt_hint="""Look for evidence of MANUFACTURING:
- "we manufacture", "our production", "made by us", "designed and built"
- Factory descriptions, OEM references, production capacity
- Do NOT confuse with distribution or reselling""",
)

SERVICES_GROUP = FieldGroup(
    name="services",
    description="Service and repair capabilities",
    fields=[
        FieldDefinition(
            name="provides_services",
            field_type="boolean",
            description="Company provides repair/maintenance/refurbishment services",
            required=True,
            default=False,
        ),
        FieldDefinition(
            name="services_gearboxes",
            field_type="boolean",
            description="Services/repairs gearboxes",
            default=False,
        ),
        FieldDefinition(
            name="services_motors",
            field_type="boolean",
            description="Services/repairs motors",
            default=False,
        ),
        FieldDefinition(
            name="services_drivetrain_accessories",
            field_type="boolean",
            description="Services/repairs drivetrain accessories",
            default=False,
        ),
        FieldDefinition(
            name="service_types",
            field_type="list",
            description="Types: repair, maintenance, refurbishment, installation, commissioning, field service",
        ),
    ],
    prompt_hint="""Look for SERVICE offerings:
- Repair services, maintenance programs, overhaul
- Service centers, field service teams
- Spare parts supply, technical support""",
)

COMPANY_INFO_GROUP = FieldGroup(
    name="company_info",
    description="Company identification and size information",
    fields=[
        FieldDefinition(
            name="company_name",
            field_type="text",
            description="Official company name",
            required=True,
        ),
        FieldDefinition(
            name="employee_count",
            field_type="integer",
            description="Number of employees (exact or estimated)",
        ),
        FieldDefinition(
            name="employee_count_range",
            field_type="enum",
            description="Employee range if exact unknown",
            enum_values=["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+", "unknown"],
            default="unknown",
        ),
        FieldDefinition(
            name="number_of_sites",
            field_type="integer",
            description="Number of company locations/facilities",
        ),
        FieldDefinition(
            name="headquarters_location",
            field_type="text",
            description="Headquarters city and country",
        ),
    ],
    prompt_hint="""Extract company information:
- Look in "About Us", footer, contact pages
- Employee counts may be approximate ("over 500 employees")
- Headquarters vs manufacturing vs sales locations""",
)

PRODUCTS_GEARBOX_GROUP = FieldGroup(
    name="products_gearbox",
    description="Gearbox product information",
    fields=[
        FieldDefinition(name="product_name", field_type="text", required=True),
        FieldDefinition(name="series_name", field_type="text"),
        FieldDefinition(name="model_number", field_type="text"),
        FieldDefinition(name="subcategory", field_type="text",
                       description="planetary, helical, worm, bevel, cycloidal"),
        FieldDefinition(name="power_rating_kw", field_type="float"),
        FieldDefinition(name="torque_rating_nm", field_type="float"),
        FieldDefinition(name="ratio", field_type="text"),
        FieldDefinition(name="efficiency_percent", field_type="float"),
    ],
    prompt_hint="""Extract GEARBOX products only:
- Product names, series, model numbers
- Convert HP to kW (multiply by 0.746)
- Convert lb-ft to Nm (multiply by 1.356)
- Gear ratios like "1:50" or "50:1" """,
    is_entity_list=True,
)

PRODUCTS_MOTOR_GROUP = FieldGroup(
    name="products_motor",
    description="Motor product information",
    fields=[
        FieldDefinition(name="product_name", field_type="text", required=True),
        FieldDefinition(name="series_name", field_type="text"),
        FieldDefinition(name="model_number", field_type="text"),
        FieldDefinition(name="subcategory", field_type="text",
                       description="AC, DC, servo, stepper, brushless, induction"),
        FieldDefinition(name="power_rating_kw", field_type="float"),
        FieldDefinition(name="speed_rating_rpm", field_type="float"),
        FieldDefinition(name="voltage", field_type="text"),
    ],
    prompt_hint="""Extract MOTOR products only:
- Electric motors, servo motors, stepper motors
- Power ratings, speed ratings, voltage""",
    is_entity_list=True,
)

PRODUCTS_ACCESSORY_GROUP = FieldGroup(
    name="products_accessory",
    description="Drivetrain accessory products",
    fields=[
        FieldDefinition(name="product_name", field_type="text", required=True),
        FieldDefinition(name="subcategory", field_type="text",
                       description="coupling, shaft, bearing, brake, clutch"),
        FieldDefinition(name="model_number", field_type="text"),
        FieldDefinition(name="torque_rating_nm", field_type="float"),
    ],
    prompt_hint="""Extract ACCESSORY products:
- Couplings, shafts, bearings, brakes, clutches
- Pulleys, belts, chains, sprockets""",
    is_entity_list=True,
)

COMPANY_META_GROUP = FieldGroup(
    name="company_meta",
    description="Certifications and locations",
    fields=[
        FieldDefinition(
            name="certifications",
            field_type="list",
            description="ISO certifications, industry standards, safety certifications",
        ),
        FieldDefinition(
            name="locations",
            field_type="list",
            description="List of {city, country, site_type} objects",
        ),
    ],
    prompt_hint="""Extract:
- Certifications: ISO 9001, ISO 14001, ATEX, UL, CE, etc.
- Locations: manufacturing plants, headquarters, sales offices, service centers""",
)


# All groups for iteration
ALL_FIELD_GROUPS = [
    MANUFACTURING_GROUP,
    SERVICES_GROUP,
    COMPANY_INFO_GROUP,
    PRODUCTS_GEARBOX_GROUP,
    PRODUCTS_MOTOR_GROUP,
    PRODUCTS_ACCESSORY_GROUP,
    COMPANY_META_GROUP,
]

# Quick lookup by name
FIELD_GROUPS_BY_NAME = {g.name: g for g in ALL_FIELD_GROUPS}
```

### 2. Create Schema-Aware LLM Extraction

**File:** `src/services/extraction/schema_extractor.py` (NEW)

```python
"""Schema-based LLM extraction with field groups."""

import json
from typing import Any

import structlog
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Settings
from services.extraction.field_groups import FieldGroup, FieldDefinition

logger = structlog.get_logger(__name__)


class SchemaExtractor:
    """Extracts structured data based on field group schemas."""

    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            timeout=settings.llm_http_timeout,
        )
        self.model = settings.llm_model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def extract_field_group(
        self,
        content: str,
        field_group: FieldGroup,
        company_name: str | None = None,
    ) -> dict[str, Any]:
        """Extract fields for a specific field group.

        Args:
            content: Markdown content to extract from.
            field_group: Field group definition.
            company_name: Optional company name for context.

        Returns:
            Dictionary of extracted field values.
        """
        system_prompt = self._build_system_prompt(field_group)
        user_prompt = self._build_user_prompt(content, field_group, company_name)

        logger.info(
            "schema_extraction_started",
            field_group=field_group.name,
            content_length=len(content),
            is_entity_list=field_group.is_entity_list,
        )

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        result_text = response.choices[0].message.content
        result_data = json.loads(result_text)

        # Apply defaults for missing fields
        result = self._apply_defaults(result_data, field_group)

        logger.info(
            "schema_extraction_completed",
            field_group=field_group.name,
            fields_extracted=len([k for k, v in result.items() if v is not None]),
        )

        return result

    def _build_system_prompt(self, field_group: FieldGroup) -> str:
        """Build system prompt for field group extraction."""
        if field_group.is_entity_list:
            return self._build_entity_list_system_prompt(field_group)

        # Build field descriptions
        field_specs = []
        for f in field_group.fields:
            spec = f'- "{f.name}" ({f.field_type}): {f.description}'
            if f.enum_values:
                spec += f' [options: {", ".join(f.enum_values)}]'
            if f.required:
                spec += " [REQUIRED]"
            field_specs.append(spec)

        fields_str = "\n".join(field_specs)

        return f"""You are extracting {field_group.description} from company documentation.

Fields to extract:
{fields_str}

{field_group.prompt_hint}

Output JSON with exactly these fields. Use null for unknown values.
For boolean fields, only return true if there is clear evidence.
"""

    def _build_entity_list_system_prompt(self, field_group: FieldGroup) -> str:
        """Build system prompt for entity list extraction (products)."""
        field_specs = []
        for f in field_group.fields:
            spec = f'- "{f.name}" ({f.field_type}): {f.description or ""}'
            field_specs.append(spec)

        fields_str = "\n".join(field_specs)

        return f"""You are extracting {field_group.description} from company documentation.

For each product found, extract:
{fields_str}

{field_group.prompt_hint}

Output JSON with structure:
{{
  "products": [
    {{"product_name": "...", "series_name": "...", ...}},
    ...
  ],
  "confidence": 0.0-1.0
}}

Only include products you find clear evidence for. Return empty list if none found.
"""

    def _build_user_prompt(
        self,
        content: str,
        field_group: FieldGroup,
        company_name: str | None,
    ) -> str:
        """Build user prompt with content."""
        company_context = f"Company: {company_name}\n\n" if company_name else ""

        return f"""{company_context}Extract {field_group.name} information from this content:

---
{content[:8000]}
---"""

    def _apply_defaults(
        self, result: dict[str, Any], field_group: FieldGroup
    ) -> dict[str, Any]:
        """Apply default values for missing fields."""
        for f in field_group.fields:
            if f.name not in result or result[f.name] is None:
                if f.default is not None:
                    result[f.name] = f.default
                elif f.field_type == "boolean":
                    result[f.name] = False
                elif f.field_type == "list":
                    result[f.name] = []

        return result
```

### 3. Create Multi-Pass Extraction Orchestrator

**File:** `src/services/extraction/schema_orchestrator.py` (NEW)

```python
"""Orchestrates multi-pass schema extraction across field groups."""

import asyncio
from uuid import UUID

import structlog

from services.extraction.field_groups import ALL_FIELD_GROUPS, FieldGroup
from services.extraction.schema_extractor import SchemaExtractor
from services.llm.chunking import chunk_document

logger = structlog.get_logger(__name__)


class SchemaExtractionOrchestrator:
    """Orchestrates extraction across all field groups for a source."""

    def __init__(self, schema_extractor: SchemaExtractor):
        self._extractor = schema_extractor

    async def extract_all_groups(
        self,
        source_id: UUID,
        markdown: str,
        company_name: str,
        field_groups: list[FieldGroup] | None = None,
    ) -> list[dict]:
        """Extract all field groups from source content.

        Args:
            source_id: Source UUID for tracking.
            markdown: Markdown content.
            company_name: Company name for context.
            field_groups: Optional specific groups (default: all).

        Returns:
            List of extraction results, one per field group.
        """
        groups = field_groups or ALL_FIELD_GROUPS
        results = []

        # Chunk document for large content
        chunks = chunk_document(markdown)

        logger.info(
            "schema_extraction_started",
            source_id=str(source_id),
            company=company_name,
            groups=len(groups),
            chunks=len(chunks),
        )

        for group in groups:
            group_result = {
                "extraction_type": group.name,
                "source_id": source_id,
                "source_group": company_name,
                "data": {},
                "confidence": 0.0,
            }

            # Extract from each chunk and merge
            chunk_results = []
            for chunk in chunks:
                try:
                    chunk_data = await self._extractor.extract_field_group(
                        content=chunk.content,
                        field_group=group,
                        company_name=company_name,
                    )
                    chunk_results.append(chunk_data)
                except Exception as e:
                    logger.warning(
                        "chunk_extraction_failed",
                        group=group.name,
                        error=str(e),
                    )

            # Merge chunk results
            if chunk_results:
                merged = self._merge_chunk_results(chunk_results, group)
                group_result["data"] = merged
                group_result["confidence"] = merged.pop("confidence", 0.8)

            results.append(group_result)

        logger.info(
            "schema_extraction_completed",
            source_id=str(source_id),
            results_count=len(results),
        )

        return results

    def _merge_chunk_results(
        self, chunk_results: list[dict], group: FieldGroup
    ) -> dict:
        """Merge results from multiple chunks.

        Aggregation rules:
        - boolean: True if ANY chunk says True
        - integer: Take maximum
        - text: Take longest non-empty
        - list: Merge and dedupe
        - products: Merge all, dedupe by product_name
        """
        if not chunk_results:
            return {}

        if group.is_entity_list:
            return self._merge_entity_lists(chunk_results)

        merged = {}
        for field in group.fields:
            values = [r.get(field.name) for r in chunk_results if r.get(field.name) is not None]

            if not values:
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
                merged[field.name] = list(dict.fromkeys(flat))
            else:  # text, enum
                merged[field.name] = max(values, key=lambda x: len(str(x)) if x else 0)

        # Average confidence
        confidences = [r.get("confidence", 0.8) for r in chunk_results]
        merged["confidence"] = sum(confidences) / len(confidences)

        return merged

    def _merge_entity_lists(self, chunk_results: list[dict]) -> dict:
        """Merge entity lists (products) from multiple chunks."""
        all_products = []
        seen_names = set()

        for result in chunk_results:
            products = result.get("products", [])
            for product in products:
                name = product.get("product_name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_products.append(product)

        confidences = [r.get("confidence", 0.8) for r in chunk_results]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.8

        return {
            "products": all_products,
            "confidence": avg_confidence,
        }
```

### 4. Create Extraction Pipeline Service

**File:** `src/services/extraction/pipeline.py` (NEW)

```python
"""Pipeline for running schema extraction on project sources."""

from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from orm_models import Source, Extraction
from services.extraction.field_groups import ALL_FIELD_GROUPS
from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator

logger = structlog.get_logger(__name__)


class SchemaExtractionPipeline:
    """Runs schema extraction on sources and stores results."""

    def __init__(
        self,
        orchestrator: SchemaExtractionOrchestrator,
        db_session: Session,
    ):
        self._orchestrator = orchestrator
        self._db = db_session

    async def extract_source(
        self,
        source: Source,
        company_name: str,
    ) -> list[Extraction]:
        """Extract all field groups from a source.

        Args:
            source: Source ORM object with markdown content.
            company_name: Company name (source_group).

        Returns:
            List of created Extraction objects.
        """
        if not source.content:
            logger.warning("source_has_no_content", source_id=str(source.id))
            return []

        # Run extraction for all field groups
        results = await self._orchestrator.extract_all_groups(
            source_id=source.id,
            markdown=source.content,
            company_name=company_name,
        )

        # Store each result as an extraction
        extractions = []
        for result in results:
            extraction = Extraction(
                project_id=source.project_id,
                source_id=source.id,
                data=result["data"],
                extraction_type=result["extraction_type"],
                source_group=company_name,
                confidence=result.get("confidence"),
                profile_used="drivetrain_schema",
            )
            self._db.add(extraction)
            extractions.append(extraction)

        self._db.flush()
        return extractions

    async def extract_project(
        self,
        project_id: UUID,
        source_groups: list[str] | None = None,
    ) -> dict:
        """Extract all sources in a project.

        Args:
            project_id: Project UUID.
            source_groups: Optional filter by company names.

        Returns:
            Summary dict with extraction counts.
        """
        query = self._db.query(Source).filter(
            Source.project_id == project_id,
            Source.status == "ready",
        )

        if source_groups:
            query = query.filter(Source.source_group.in_(source_groups))

        sources = query.all()

        logger.info(
            "project_extraction_started",
            project_id=str(project_id),
            source_count=len(sources),
        )

        total_extractions = 0
        for source in sources:
            extractions = await self.extract_source(
                source=source,
                company_name=source.source_group,
            )
            total_extractions += len(extractions)

        self._db.commit()

        return {
            "project_id": str(project_id),
            "sources_processed": len(sources),
            "extractions_created": total_extractions,
            "field_groups": len(ALL_FIELD_GROUPS),
        }
```

### 5. Create Schema Table Report Generator

**File:** `src/services/reports/schema_table.py` (NEW)

```python
"""Generate tabular reports from schema extractions."""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from orm_models import Extraction
from services.extraction.field_groups import (
    ALL_FIELD_GROUPS,
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

    def _merge_field_group_data(
        self, data_list: list[dict], group
    ) -> dict[str, Any]:
        """Merge multiple extraction data dicts for a field group."""
        merged = {}

        for field in group.fields:
            values = [d.get(field.name) for d in data_list if d.get(field.name) is not None]

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
                merged[field.name] = list(dict.fromkeys(flat))
            else:
                # Text - concatenate unique non-empty values
                unique = list(dict.fromkeys([str(v) for v in values if v]))
                merged[field.name] = "; ".join(unique) if len(unique) > 1 else (unique[0] if unique else None)

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

    def _build_markdown_table(
        self, rows: list[dict], columns: list[str]
    ) -> str:
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
```

### 6. Add API Endpoint for Schema Extraction

**File:** `src/api/v1/extraction.py` (MODIFY)

Add new endpoint:

```python
from services.extraction.schema_extractor import SchemaExtractor
from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator
from services.extraction.pipeline import SchemaExtractionPipeline


@router.post("/projects/{project_id}/extract-schema")
async def extract_schema(
    project_id: UUID,
    source_groups: list[str] | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Run schema-based extraction on project sources.

    This uses the drivetrain company template with 7 field groups,
    running multiple focused LLM calls per source.

    Args:
        project_id: Project UUID.
        source_groups: Optional filter by company names.

    Returns:
        Summary of extraction results.
    """
    # Validate project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Create extraction pipeline
    extractor = SchemaExtractor(settings)
    orchestrator = SchemaExtractionOrchestrator(extractor)
    pipeline = SchemaExtractionPipeline(orchestrator, db)

    # Run extraction
    result = await pipeline.extract_project(
        project_id=project_id,
        source_groups=source_groups,
    )

    return result
```

### 7. Add Schema Table Report Type

**File:** `src/models.py` (MODIFY)

```python
class ReportType(str, Enum):
    SINGLE = "single"
    COMPARISON = "comparison"
    TABLE = "table"
    SCHEMA_TABLE = "schema_table"  # NEW
```

### 8. Wire Up Schema Table in Report Service

**File:** `src/services/reports/service.py` (MODIFY)

Add handling for `SCHEMA_TABLE` type:

```python
from services.reports.schema_table import SchemaTableReport

# In generate() method:
elif request.type == ReportType.SCHEMA_TABLE:
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
    title = request.title or f"Schema Report: {', '.join(request.source_groups)}"
```

## Tests to Write

**File:** `tests/test_schema_extractor.py` (NEW)

```python
"""Tests for schema-based extraction."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.extraction.field_groups import MANUFACTURING_GROUP, PRODUCTS_GEARBOX_GROUP
from services.extraction.schema_extractor import SchemaExtractor


class TestSchemaExtractor:
    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        return settings

    async def test_extract_manufacturing_booleans(self, mock_settings):
        """Test extraction of boolean manufacturing fields."""
        extractor = SchemaExtractor(mock_settings)

        # Mock the OpenAI response
        extractor.client = MagicMock()
        extractor.client.chat.completions.create = AsyncMock(return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"manufactures_gearboxes": true, "manufactures_motors": false}'))]
        ))

        result = await extractor.extract_field_group(
            content="We manufacture planetary gearboxes.",
            field_group=MANUFACTURING_GROUP,
            company_name="Test Company",
        )

        assert result["manufactures_gearboxes"] is True
        assert result["manufactures_motors"] is False

    async def test_extract_product_list(self, mock_settings):
        """Test extraction of product entity list."""
        extractor = SchemaExtractor(mock_settings)

        extractor.client = MagicMock()
        extractor.client.chat.completions.create = AsyncMock(return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"products": [{"product_name": "D Series", "power_rating_kw": 100}]}'))]
        ))

        result = await extractor.extract_field_group(
            content="Our D Series gearbox offers 100kW.",
            field_group=PRODUCTS_GEARBOX_GROUP,
        )

        assert len(result["products"]) == 1
        assert result["products"][0]["product_name"] == "D Series"
```

**File:** `tests/test_schema_table_report.py` (NEW)

```python
"""Tests for schema table report generation."""

import pytest
from unittest.mock import MagicMock
from uuid import uuid4

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
```

## Verification

1. **Run tests:**
   ```bash
   cd src && pytest tests/test_schema_extractor.py tests/test_schema_table_report.py -v
   ```

2. **Run linting:**
   ```bash
   ruff check src/services/extraction/ src/services/reports/schema_table.py
   ruff format src/services/extraction/ src/services/reports/schema_table.py
   ```

3. **Manual test - run extraction:**
   ```bash
   curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/extract-schema" \
     -H "X-API-Key: $API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"source_groups": ["Brevini", "EMAG"]}'
   ```

4. **Manual test - generate schema table report:**
   ```bash
   curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/reports" \
     -H "X-API-Key: $API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "type": "schema_table",
       "source_groups": ["Brevini", "EMAG"],
       "output_format": "xlsx"
     }'
   ```

## Constraints

- Do NOT modify existing extraction logic for backward compatibility
- Do NOT add database migrations - use existing JSONB columns
- Do NOT modify the DRIVETRAIN_COMPANY_TEMPLATE in templates.py
- Keep LLM calls focused (3-8 fields per call max)
- Handle LLM failures gracefully (continue with other groups)

## Files Summary

| File | Action |
|------|--------|
| `src/services/extraction/field_groups.py` | CREATE |
| `src/services/extraction/schema_extractor.py` | CREATE |
| `src/services/extraction/schema_orchestrator.py` | CREATE |
| `src/services/extraction/pipeline.py` | CREATE |
| `src/services/reports/schema_table.py` | CREATE |
| `src/api/v1/extraction.py` | MODIFY - add endpoint |
| `src/models.py` | MODIFY - add SCHEMA_TABLE type |
| `src/services/reports/service.py` | MODIFY - wire up schema_table |
| `tests/test_schema_extractor.py` | CREATE |
| `tests/test_schema_table_report.py` | CREATE |
