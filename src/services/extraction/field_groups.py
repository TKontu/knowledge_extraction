"""Field group definitions for schema-based extraction."""

from dataclasses import dataclass
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
            name="provides_field_service",
            field_type="boolean",
            description="Provides on-site/field service at customer locations",
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
- Service centers, field service teams (on-site service at customer locations)
- Spare parts supply, technical support
- Field service = technicians travel to customer site""",
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
            enum_values=[
                "1-10",
                "11-50",
                "51-200",
                "201-500",
                "501-1000",
                "1001-5000",
                "5000+",
                "unknown",
            ],
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
        FieldDefinition(
            name="product_name",
            field_type="text",
            description="Product name",
            required=True,
        ),
        FieldDefinition(
            name="series_name", field_type="text", description="Product series"
        ),
        FieldDefinition(
            name="model_number", field_type="text", description="Model number"
        ),
        FieldDefinition(
            name="subcategory",
            field_type="text",
            description="planetary, helical, worm, bevel, cycloidal",
        ),
        FieldDefinition(
            name="power_rating_kw", field_type="float", description="Power rating in kW"
        ),
        FieldDefinition(
            name="torque_rating_nm",
            field_type="float",
            description="Torque rating in Nm",
        ),
        FieldDefinition(name="ratio", field_type="text", description="Gear ratio"),
        FieldDefinition(
            name="efficiency_percent",
            field_type="float",
            description="Efficiency percentage",
        ),
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
        FieldDefinition(
            name="product_name",
            field_type="text",
            description="Product name",
            required=True,
        ),
        FieldDefinition(
            name="series_name", field_type="text", description="Product series"
        ),
        FieldDefinition(
            name="model_number", field_type="text", description="Model number"
        ),
        FieldDefinition(
            name="subcategory",
            field_type="text",
            description="AC, DC, servo, stepper, brushless, induction",
        ),
        FieldDefinition(
            name="power_rating_kw", field_type="float", description="Power rating in kW"
        ),
        FieldDefinition(
            name="speed_rating_rpm",
            field_type="float",
            description="Speed rating in RPM",
        ),
        FieldDefinition(name="voltage", field_type="text", description="Voltage"),
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
        FieldDefinition(
            name="product_name",
            field_type="text",
            description="Product name",
            required=True,
        ),
        FieldDefinition(
            name="subcategory",
            field_type="text",
            description="coupling, shaft, bearing, brake, clutch",
        ),
        FieldDefinition(
            name="model_number", field_type="text", description="Model number"
        ),
        FieldDefinition(
            name="torque_rating_nm",
            field_type="float",
            description="Torque rating in Nm",
        ),
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


# DEPRECATED: These hardcoded groups are no longer used.
# Field groups are now loaded dynamically from project.extraction_schema
# via SchemaAdapter.convert_to_field_groups().
# Kept for backward compatibility with any external code that might reference them.
ALL_FIELD_GROUPS = [
    MANUFACTURING_GROUP,
    SERVICES_GROUP,
    COMPANY_INFO_GROUP,
    PRODUCTS_GEARBOX_GROUP,
    PRODUCTS_MOTOR_GROUP,
    PRODUCTS_ACCESSORY_GROUP,
    COMPANY_META_GROUP,
]

# DEPRECATED: Use SchemaAdapter to convert project schemas instead.
FIELD_GROUPS_BY_NAME = {g.name: g for g in ALL_FIELD_GROUPS}
