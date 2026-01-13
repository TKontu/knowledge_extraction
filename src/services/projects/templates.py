"""Project templates for common extraction use cases."""

# Template for company technical analysis (default project)
COMPANY_ANALYSIS_TEMPLATE = {
    "name": "company_analysis",
    "description": "Extract technical facts from company documentation",
    "source_config": {"type": "web", "group_by": "company"},
    "extraction_schema": {
        "name": "technical_fact",
        "fields": [
            {
                "name": "fact_text",
                "type": "text",
                "required": True,
                "description": "The extracted factual statement",
            },
            {
                "name": "category",
                "type": "enum",
                "required": True,
                "values": [
                    "specs",
                    "api",
                    "security",
                    "pricing",
                    "features",
                    "integration",
                ],
                "description": "Category of the technical fact",
            },
            {
                "name": "confidence",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.8,
                "description": "Confidence score of the extraction",
            },
            {
                "name": "source_quote",
                "type": "text",
                "required": False,
                "description": "Brief quote from the source document",
            },
        ],
    },
    "entity_types": [
        {"name": "plan", "description": "Pricing tier or plan"},
        {"name": "feature", "description": "Product capability"},
        {
            "name": "limit",
            "description": "Quota or threshold",
            "attributes": [
                {"name": "numeric_value", "type": "number"},
                {"name": "unit", "type": "text"},
            ],
        },
        {
            "name": "certification",
            "description": "Security or compliance certification",
        },
        {"name": "pricing", "description": "Cost or price point"},
    ],
    "prompt_templates": {},
    "is_template": True,
}

# Template for academic paper extraction
RESEARCH_SURVEY_TEMPLATE = {
    "name": "research_survey",
    "description": "Extract key information from academic papers and research documents",
    "source_config": {"type": "web", "group_by": "paper"},
    "extraction_schema": {
        "name": "research_finding",
        "fields": [
            {
                "name": "finding_text",
                "type": "text",
                "required": True,
                "description": "Key finding or claim",
            },
            {
                "name": "category",
                "type": "enum",
                "required": True,
                "values": [
                    "methodology",
                    "result",
                    "conclusion",
                    "limitation",
                    "future_work",
                ],
                "description": "Category of the finding",
            },
            {
                "name": "confidence",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.8,
            },
            {"name": "source_quote", "type": "text", "required": False},
        ],
    },
    "entity_types": [
        {"name": "author", "description": "Paper author or researcher"},
        {
            "name": "institution",
            "description": "University or research organization",
        },
        {"name": "method", "description": "Research methodology or technique"},
        {
            "name": "metric",
            "description": "Quantitative result or measurement",
            "attributes": [
                {"name": "value", "type": "number"},
                {"name": "unit", "type": "text"},
            ],
        },
        {"name": "dataset", "description": "Dataset used in research"},
        {"name": "citation", "description": "Referenced work"},
    ],
    "prompt_templates": {},
    "is_template": True,
}

# Template for legal document extraction
CONTRACT_REVIEW_TEMPLATE = {
    "name": "contract_review",
    "description": "Extract key terms and obligations from legal contracts",
    "source_config": {"type": "document", "group_by": "contract"},
    "extraction_schema": {
        "name": "contract_term",
        "fields": [
            {
                "name": "term_text",
                "type": "text",
                "required": True,
                "description": "Contract term or clause",
            },
            {
                "name": "category",
                "type": "enum",
                "required": True,
                "values": [
                    "obligation",
                    "right",
                    "condition",
                    "definition",
                    "termination",
                    "liability",
                ],
                "description": "Type of contract term",
            },
            {
                "name": "confidence",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.8,
            },
            {
                "name": "section_ref",
                "type": "text",
                "required": False,
                "description": "Section or clause number",
            },
        ],
    },
    "entity_types": [
        {"name": "party", "description": "Contract party (person or organization)"},
        {
            "name": "date",
            "description": "Significant date (effective, expiration, deadline)",
            "attributes": [{"name": "date_type", "type": "text"}],
        },
        {
            "name": "amount",
            "description": "Monetary amount or payment term",
            "attributes": [
                {"name": "value", "type": "number"},
                {"name": "currency", "type": "text"},
            ],
        },
        {"name": "duration", "description": "Time period or term length"},
        {"name": "jurisdiction", "description": "Governing law or venue"},
    ],
    "prompt_templates": {},
    "is_template": True,
}

# Template for book catalog extraction
BOOK_CATALOG_TEMPLATE = {
    "name": "book_catalog",
    "description": "Extract book information from online bookstores and catalogs",
    "source_config": {"type": "web", "group_by": "category"},
    "extraction_schema": {
        "name": "book_info",
        "fields": [
            {
                "name": "title",
                "type": "text",
                "required": True,
                "description": "Book title",
            },
            {
                "name": "price",
                "type": "text",
                "required": True,
                "description": "Book price with currency",
            },
            {
                "name": "availability",
                "type": "enum",
                "required": False,
                "values": ["in_stock", "out_of_stock", "limited", "unknown"],
                "default": "unknown",
                "description": "Stock availability status",
            },
            {
                "name": "rating",
                "type": "integer",
                "required": False,
                "min": 1,
                "max": 5,
                "description": "Star rating (1-5)",
            },
            {
                "name": "category",
                "type": "text",
                "required": False,
                "description": "Book genre or category",
            },
            {
                "name": "description",
                "type": "text",
                "required": False,
                "description": "Book description or synopsis",
            },
            {
                "name": "confidence",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.8,
            },
        ],
    },
    "entity_types": [
        {"name": "book", "description": "Book title"},
        {"name": "author", "description": "Book author"},
        {
            "name": "price",
            "description": "Book price",
            "attributes": [
                {"name": "amount", "type": "number"},
                {"name": "currency", "type": "text"},
            ],
        },
        {"name": "category", "description": "Book genre or category"},
        {"name": "publisher", "description": "Book publisher"},
    ],
    "prompt_templates": {},
    "is_template": True,
}

# Project template for industrial drivetrain component company extraction.
DRIVETRAIN_COMPANY_TEMPLATE = {
    "name": "drivetrain_company_analysis",
    "description": "Extract manufacturing capabilities, services, company info, and product specs from drivetrain component companies",
    "source_config": {"type": "web", "group_by": "company"},
    "extraction_schema": {
        "name": "company_profile",
        "fields": [
            # Manufacturing capabilities
            {
                "name": "manufactures_gearboxes",
                "type": "boolean",
                "required": True,
                "default": False,
                "description": "Whether the company manufactures gearboxes/gearheads/gear reducers",
            },
            {
                "name": "manufactures_motors",
                "type": "boolean",
                "required": True,
                "default": False,
                "description": "Whether the company manufactures motors (electric, servo, stepper, etc.)",
            },
            {
                "name": "manufactures_drivetrain_accessories",
                "type": "boolean",
                "required": True,
                "default": False,
                "description": "Whether the company manufactures drivetrain accessories (couplings, shafts, bearings, brakes, clutches, etc.)",
            },
            {
                "name": "manufacturing_details",
                "type": "text",
                "required": False,
                "description": "Additional details about manufacturing capabilities and specializations",
            },
            # Service capabilities
            {
                "name": "provides_services",
                "type": "boolean",
                "required": True,
                "default": False,
                "description": "Whether the company provides repair/maintenance/refurbishment services",
            },
            {
                "name": "services_gearboxes",
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Provides service for gearboxes",
            },
            {
                "name": "services_motors",
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Provides service for motors",
            },
            {
                "name": "services_drivetrain_accessories",
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Provides service for drivetrain accessories",
            },
            {
                "name": "service_types",
                "type": "list",
                "required": False,
                "description": "Types of services offered (repair, maintenance, refurbishment, installation, commissioning, etc.)",
            },
            # Company information
            {
                "name": "company_name",
                "type": "text",
                "required": True,
                "description": "Official company name",
            },
            {
                "name": "employee_count",
                "type": "integer",
                "required": False,
                "description": "Number of employees (exact or estimated)",
            },
            {
                "name": "employee_count_range",
                "type": "enum",
                "required": False,
                "values": [
                    "1-10",
                    "11-50",
                    "51-200",
                    "201-500",
                    "501-1000",
                    "1001-5000",
                    "5000+",
                    "unknown",
                ],
                "default": "unknown",
                "description": "Employee count range if exact number unavailable",
            },
            {
                "name": "number_of_sites",
                "type": "integer",
                "required": False,
                "description": "Number of company locations/facilities",
            },
            {
                "name": "headquarters_location",
                "type": "text",
                "required": False,
                "description": "Headquarters city and country",
            },
            # Confidence and source
            {
                "name": "confidence",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.8,
                "description": "Confidence score of the extraction",
            },
            {
                "name": "source_url",
                "type": "text",
                "required": False,
                "description": "Source URL for the extracted information",
            },
        ],
    },
    "entity_types": [
        # Company entities
        {
            "name": "company",
            "description": "Company name and identifier",
            "attributes": [
                {"name": "legal_name", "type": "text"},
                {"name": "trade_name", "type": "text"},
            ],
        },
        # Location entities
        {
            "name": "site_location",
            "description": "Company facility or office location",
            "attributes": [
                {"name": "city", "type": "text"},
                {"name": "state_province", "type": "text"},
                {"name": "country", "type": "text"},
                {"name": "site_type", "type": "text"},  # headquarters, manufacturing, sales, service center
            ],
        },
        # Product entities
        {
            "name": "product",
            "description": "Product offered by the company",
            "attributes": [
                {"name": "product_name", "type": "text"},
                {"name": "product_category", "type": "text"},  # gearbox, motor, accessory
                {"name": "product_subcategory", "type": "text"},  # e.g., planetary gearbox, servo motor
                {"name": "power_rating_kw", "type": "number"},
                {"name": "power_rating_hp", "type": "number"},
                {"name": "torque_rating_nm", "type": "number"},
                {"name": "torque_rating_lb_ft", "type": "number"},
                {"name": "speed_rating_rpm", "type": "number"},
                {"name": "speed_range_min_rpm", "type": "number"},
                {"name": "speed_range_max_rpm", "type": "number"},
                {"name": "ratio", "type": "text"},  # gear ratio for gearboxes
                {"name": "efficiency_percent", "type": "number"},
                {"name": "model_number", "type": "text"},
                {"name": "series_name", "type": "text"},
            ],
        },
        # Service entities
        {
            "name": "service",
            "description": "Service offered by the company",
            "attributes": [
                {"name": "service_name", "type": "text"},
                {"name": "service_type", "type": "text"},  # repair, maintenance, refurbishment, etc.
                {"name": "components_serviced", "type": "text"},  # gearbox, motor, etc.
            ],
        },
        # Specification entities
        {
            "name": "specification",
            "description": "Technical specification",
            "attributes": [
                {"name": "spec_name", "type": "text"},
                {"name": "value", "type": "number"},
                {"name": "unit", "type": "text"},
                {"name": "product_ref", "type": "text"},
            ],
        },
        # Certification entities
        {
            "name": "certification",
            "description": "Industry certification or standard compliance",
            "attributes": [
                {"name": "cert_name", "type": "text"},
                {"name": "cert_body", "type": "text"},
            ],
        },
    ],
    "prompt_templates": {
        "system": """You are extracting information about industrial drivetrain component companies.
Focus on identifying:
1. Manufacturing capabilities for gearboxes, motors, and drivetrain accessories
2. Service capabilities for these components
3. Company size and locations
4. Product specifications including power, torque, and speed ratings

Be precise with technical specifications and convert units where possible to standard metric (kW, Nm, RPM).
If information is not explicitly stated, mark confidence as lower.""",
        "manufacturing_detection": """Analyze the content to determine if this company MANUFACTURES any of:
- Gearboxes (including gear reducers, gearheads, planetary gears, helical gears, worm gears, bevel gears)
- Motors (including electric motors, servo motors, stepper motors, AC/DC motors, brushless motors)
- Drivetrain accessories (including couplings, shafts, bearings, brakes, clutches, pulleys, belts, chains, sprockets)

Look for terms like: "we manufacture", "our production", "made by us", "designed and built", factory descriptions, OEM references.""",
        "service_detection": """Analyze the content to determine if this company SERVICES/REPAIRS any of:
- Gearboxes
- Motors
- Drivetrain accessories

Look for: repair services, maintenance programs, refurbishment, overhaul, service centers, field service, spare parts.""",
        "product_extraction": """Extract product information including:
- Product name and model/series
- Category (gearbox/motor/accessory)
- Power rating (convert to kW if given in HP: multiply by 0.746)
- Torque rating (convert to Nm if given in lb-ft: multiply by 1.356)
- Speed rating in RPM
- Gear ratios for gearboxes""",
    },
    "is_template": True,
}

# Simplified version focusing on key fields for faster extraction
DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE = {
    "name": "drivetrain_company_simple",
    "description": "Simplified extraction for drivetrain component companies - key facts only",
    "source_config": {"type": "web", "group_by": "company"},
    "extraction_schema": {
        "name": "company_summary",
        "fields": [
            {
                "name": "company_name",
                "type": "text",
                "required": True,
                "description": "Company name",
            },
            {
                "name": "business_type",
                "type": "enum",
                "required": True,
                "values": [
                    "manufacturer",
                    "service_provider",
                    "distributor",
                    "both_mfg_and_service",
                    "unknown",
                ],
                "description": "Primary business type",
            },
            {
                "name": "product_categories",
                "type": "list",
                "required": True,
                "description": "List of product categories: gearboxes, motors, drivetrain_accessories",
            },
            {
                "name": "service_categories",
                "type": "list",
                "required": False,
                "description": "List of serviced categories: gearboxes, motors, drivetrain_accessories",
            },
            {
                "name": "employee_count",
                "type": "text",
                "required": False,
                "description": "Number of employees or range",
            },
            {
                "name": "site_count",
                "type": "integer",
                "required": False,
                "description": "Number of locations",
            },
            {
                "name": "site_locations",
                "type": "list",
                "required": False,
                "description": "List of site locations (city, country)",
            },
            {
                "name": "products",
                "type": "list",
                "required": False,
                "description": "List of products with specs",
            },
            {
                "name": "confidence",
                "type": "float",
                "min": 0.0,
                "max": 1.0,
                "default": 0.8,
            },
        ],
    },
    "entity_types": [
        {
            "name": "product",
            "description": "Product with specifications",
            "attributes": [
                {"name": "name", "type": "text"},
                {"name": "category", "type": "text"},
                {"name": "power_kw", "type": "number"},
                {"name": "torque_nm", "type": "number"},
                {"name": "speed_rpm", "type": "number"},
            ],
        },
        {
            "name": "location",
            "description": "Site location",
            "attributes": [
                {"name": "city", "type": "text"},
                {"name": "country", "type": "text"},
                {"name": "type", "type": "text"},
            ],
        },
    ],
    "prompt_templates": {},
    "is_template": True,
}

__all__ = [
    "COMPANY_ANALYSIS_TEMPLATE",
    "RESEARCH_SURVEY_TEMPLATE",
    "CONTRACT_REVIEW_TEMPLATE",
    "BOOK_CATALOG_TEMPLATE",
    "DRIVETRAIN_COMPANY_TEMPLATE",
    "DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE",
]
