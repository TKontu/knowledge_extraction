"""Project templates for common extraction use cases.

All templates use the new schema format with:
- field_groups[] wrapper
- field_type (not type)
- enum_values (not values)
"""

# Template for company technical analysis (default project)
COMPANY_ANALYSIS_TEMPLATE = {
    "name": "company_analysis",
    "description": "Extract technical facts from company documentation",
    "source_config": {"type": "web", "group_by": "company"},
    "extraction_context": {
        "source_type": "company website",
        "source_label": "Company",
        "entity_id_fields": ["entity_id", "name", "id", "product_name"],
    },
    "extraction_schema": {
        "name": "technical_fact",
        "version": "1.0",
        "field_groups": [
            {
                "name": "technical_facts",
                "description": "Technical facts extracted from company documentation",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "fact_text",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "The extracted factual statement",
                    },
                    {
                        "name": "category",
                        "field_type": "enum",
                        "required": True,
                        "default": "features",
                        "enum_values": [
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
                        "field_type": "float",
                        "required": True,
                        "default": 0.8,
                        "description": "Confidence score of the extraction",
                    },
                    {
                        "name": "source_quote",
                        "field_type": "text",
                        "required": False,
                        "description": "Brief quote from the source document",
                    },
                ],
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
    "extraction_context": {
        "source_type": "research paper",
        "source_label": "Paper",
        "entity_id_fields": ["entity_id", "name", "id"],
    },
    "extraction_schema": {
        "name": "research_finding",
        "version": "1.0",
        "field_groups": [
            {
                "name": "research_findings",
                "description": "Key findings from academic papers",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "finding_text",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Key finding or claim",
                    },
                    {
                        "name": "category",
                        "field_type": "enum",
                        "required": True,
                        "default": "result",
                        "enum_values": [
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
                        "field_type": "float",
                        "required": True,
                        "default": 0.8,
                        "description": "Confidence score",
                    },
                    {
                        "name": "source_quote",
                        "field_type": "text",
                        "required": False,
                        "description": "Source quote",
                    },
                ],
            },
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
    "extraction_context": {
        "source_type": "legal contract",
        "source_label": "Contract",
        "entity_id_fields": ["entity_id", "name", "id"],
    },
    "extraction_schema": {
        "name": "contract_term",
        "version": "1.0",
        "field_groups": [
            {
                "name": "contract_terms",
                "description": "Key terms and clauses from contracts",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "term_text",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Contract term or clause",
                    },
                    {
                        "name": "category",
                        "field_type": "enum",
                        "required": True,
                        "default": "obligation",
                        "enum_values": [
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
                        "field_type": "float",
                        "required": True,
                        "default": 0.8,
                        "description": "Confidence score",
                    },
                    {
                        "name": "section_ref",
                        "field_type": "text",
                        "required": False,
                        "description": "Section or clause number",
                    },
                ],
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
    "extraction_context": {
        "source_type": "book catalog",
        "source_label": "Catalog",
        "entity_id_fields": ["entity_id", "name", "id"],
    },
    "extraction_schema": {
        "name": "book_info",
        "version": "1.0",
        "field_groups": [
            {
                "name": "book_details",
                "description": "Book information from catalogs",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "title",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Book title",
                    },
                    {
                        "name": "price",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Book price with currency",
                    },
                    {
                        "name": "availability",
                        "field_type": "enum",
                        "required": True,
                        "default": "unknown",
                        "enum_values": ["in_stock", "out_of_stock", "limited", "unknown"],
                        "description": "Stock availability status",
                    },
                    {
                        "name": "rating",
                        "field_type": "integer",
                        "required": False,
                        "description": "Star rating (1-5)",
                    },
                    {
                        "name": "category",
                        "field_type": "text",
                        "required": False,
                        "description": "Book genre or category",
                    },
                    {
                        "name": "description",
                        "field_type": "text",
                        "required": False,
                        "description": "Book description or synopsis",
                    },
                    {
                        "name": "confidence",
                        "field_type": "float",
                        "required": True,
                        "default": 0.8,
                        "description": "Confidence score",
                    },
                ],
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
    "extraction_context": {
        "source_type": "company documentation",
        "source_label": "Company",
        "entity_id_fields": ["product_name", "entity_id", "name", "id"],
    },
    "extraction_schema": {
        "name": "company_profile",
        "version": "1.0",
        "field_groups": [
            {
                "name": "manufacturing",
                "description": "Manufacturing capabilities for drivetrain components",
                "is_entity_list": False,
                "prompt_hint": """Look for evidence of MANUFACTURING:
- "we manufacture", "our production", "made by us", "designed and built"
- Factory descriptions, OEM references, production capacity
- Do NOT confuse with distribution or reselling""",
                "fields": [
                    {
                        "name": "manufactures_gearboxes",
                        "field_type": "boolean",
                        "required": True,
                        "default": False,
                        "description": "Whether the company manufactures gearboxes/gearheads/gear reducers",
                    },
                    {
                        "name": "manufactures_motors",
                        "field_type": "boolean",
                        "required": True,
                        "default": False,
                        "description": "Whether the company manufactures motors (electric, servo, stepper, etc.)",
                    },
                    {
                        "name": "manufactures_drivetrain_accessories",
                        "field_type": "boolean",
                        "required": True,
                        "default": False,
                        "description": "Whether the company manufactures drivetrain accessories (couplings, shafts, bearings, brakes, clutches, etc.)",
                    },
                    {
                        "name": "manufacturing_details",
                        "field_type": "text",
                        "required": False,
                        "description": "Additional details about manufacturing capabilities and specializations",
                    },
                ],
            },
            {
                "name": "services",
                "description": "Service and repair capabilities",
                "is_entity_list": False,
                "prompt_hint": """Look for SERVICE offerings:
- Repair services, maintenance programs, overhaul
- Service centers, field service teams (on-site service at customer locations)
- Spare parts supply, technical support
- Field service = technicians travel to customer site""",
                "fields": [
                    {
                        "name": "provides_services",
                        "field_type": "boolean",
                        "required": True,
                        "default": False,
                        "description": "Whether the company provides repair/maintenance/refurbishment services",
                    },
                    {
                        "name": "services_gearboxes",
                        "field_type": "boolean",
                        "required": True,
                        "default": False,
                        "description": "Provides service for gearboxes",
                    },
                    {
                        "name": "services_motors",
                        "field_type": "boolean",
                        "required": True,
                        "default": False,
                        "description": "Provides service for motors",
                    },
                    {
                        "name": "services_drivetrain_accessories",
                        "field_type": "boolean",
                        "required": True,
                        "default": False,
                        "description": "Provides service for drivetrain accessories",
                    },
                    {
                        "name": "provides_field_service",
                        "field_type": "boolean",
                        "required": True,
                        "default": False,
                        "description": "Provides on-site/field service at customer locations",
                    },
                    {
                        "name": "service_types",
                        "field_type": "list",
                        "required": False,
                        "description": "Types: repair, maintenance, refurbishment, installation, commissioning, field service",
                    },
                ],
            },
            {
                "name": "company_info",
                "description": "Company identification and size information",
                "is_entity_list": False,
                "prompt_hint": """Extract company information:
- Look in "About Us", footer, contact pages
- Employee counts may be approximate ("over 500 employees")
- Headquarters vs manufacturing vs sales locations""",
                "fields": [
                    {
                        "name": "company_name",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Official company name",
                    },
                    {
                        "name": "employee_count",
                        "field_type": "integer",
                        "required": False,
                        "description": "Number of employees (exact or estimated)",
                    },
                    {
                        "name": "employee_count_range",
                        "field_type": "enum",
                        "required": True,
                        "default": "unknown",
                        "enum_values": [
                            "1-10",
                            "11-50",
                            "51-200",
                            "201-500",
                            "501-1000",
                            "1001-5000",
                            "5000+",
                            "unknown",
                        ],
                        "description": "Employee count range if exact number unavailable",
                    },
                    {
                        "name": "number_of_sites",
                        "field_type": "integer",
                        "required": False,
                        "description": "Number of company locations/facilities",
                    },
                    {
                        "name": "headquarters_location",
                        "field_type": "text",
                        "required": False,
                        "description": "Headquarters city and country",
                    },
                ],
            },
            {
                "name": "products_gearbox",
                "description": "Gearbox product information",
                "is_entity_list": True,
                "prompt_hint": """Extract GEARBOX products only:
- Product names, series, model numbers
- Convert HP to kW (multiply by 0.746)
- Convert lb-ft to Nm (multiply by 1.356)
- Gear ratios like "1:50" or "50:1" """,
                "fields": [
                    {
                        "name": "product_name",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Product name",
                    },
                    {
                        "name": "series_name",
                        "field_type": "text",
                        "required": False,
                        "description": "Product series",
                    },
                    {
                        "name": "model_number",
                        "field_type": "text",
                        "required": False,
                        "description": "Model number",
                    },
                    {
                        "name": "subcategory",
                        "field_type": "text",
                        "required": False,
                        "description": "planetary, helical, worm, bevel, cycloidal",
                    },
                    {
                        "name": "power_rating_kw",
                        "field_type": "float",
                        "required": False,
                        "description": "Power rating in kW",
                    },
                    {
                        "name": "torque_rating_nm",
                        "field_type": "float",
                        "required": False,
                        "description": "Torque rating in Nm",
                    },
                    {
                        "name": "ratio",
                        "field_type": "text",
                        "required": False,
                        "description": "Gear ratio",
                    },
                    {
                        "name": "efficiency_percent",
                        "field_type": "float",
                        "required": False,
                        "description": "Efficiency percentage",
                    },
                ],
            },
            {
                "name": "products_motor",
                "description": "Motor product information",
                "is_entity_list": True,
                "prompt_hint": """Extract MOTOR products only:
- Electric motors, servo motors, stepper motors
- Power ratings, speed ratings, voltage""",
                "fields": [
                    {
                        "name": "product_name",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Product name",
                    },
                    {
                        "name": "series_name",
                        "field_type": "text",
                        "required": False,
                        "description": "Product series",
                    },
                    {
                        "name": "model_number",
                        "field_type": "text",
                        "required": False,
                        "description": "Model number",
                    },
                    {
                        "name": "subcategory",
                        "field_type": "text",
                        "required": False,
                        "description": "AC, DC, servo, stepper, brushless, induction",
                    },
                    {
                        "name": "power_rating_kw",
                        "field_type": "float",
                        "required": False,
                        "description": "Power rating in kW",
                    },
                    {
                        "name": "speed_rating_rpm",
                        "field_type": "float",
                        "required": False,
                        "description": "Speed rating in RPM",
                    },
                    {
                        "name": "voltage",
                        "field_type": "text",
                        "required": False,
                        "description": "Voltage",
                    },
                ],
            },
            {
                "name": "products_accessory",
                "description": "Drivetrain accessory products",
                "is_entity_list": True,
                "prompt_hint": """Extract ACCESSORY products:
- Couplings, shafts, bearings, brakes, clutches
- Pulleys, belts, chains, sprockets""",
                "fields": [
                    {
                        "name": "product_name",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Product name",
                    },
                    {
                        "name": "subcategory",
                        "field_type": "text",
                        "required": False,
                        "description": "coupling, shaft, bearing, brake, clutch",
                    },
                    {
                        "name": "model_number",
                        "field_type": "text",
                        "required": False,
                        "description": "Model number",
                    },
                    {
                        "name": "torque_rating_nm",
                        "field_type": "float",
                        "required": False,
                        "description": "Torque rating in Nm",
                    },
                ],
            },
            {
                "name": "company_meta",
                "description": "Certifications and locations",
                "is_entity_list": False,
                "prompt_hint": """Extract:
- Certifications: ISO 9001, ISO 14001, ATEX, UL, CE, etc.
- Locations: manufacturing plants, headquarters, sales offices, service centers""",
                "fields": [
                    {
                        "name": "certifications",
                        "field_type": "list",
                        "required": False,
                        "description": "ISO certifications, industry standards, safety certifications",
                    },
                    {
                        "name": "locations",
                        "field_type": "list",
                        "required": False,
                        "description": "List of {city, country, site_type} objects",
                    },
                ],
            },
        ],
    },
    "entity_types": [
        {
            "name": "company",
            "description": "Company name and identifier",
            "attributes": [
                {"name": "legal_name", "type": "text"},
                {"name": "trade_name", "type": "text"},
            ],
        },
        {
            "name": "site_location",
            "description": "Company facility or office location",
            "attributes": [
                {"name": "city", "type": "text"},
                {"name": "state_province", "type": "text"},
                {"name": "country", "type": "text"},
                {"name": "site_type", "type": "text"},
            ],
        },
        {
            "name": "product",
            "description": "Product offered by the company",
            "attributes": [
                {"name": "product_name", "type": "text"},
                {"name": "product_category", "type": "text"},
                {"name": "product_subcategory", "type": "text"},
                {"name": "power_rating_kw", "type": "number"},
                {"name": "torque_rating_nm", "type": "number"},
                {"name": "speed_rating_rpm", "type": "number"},
                {"name": "ratio", "type": "text"},
                {"name": "model_number", "type": "text"},
                {"name": "series_name", "type": "text"},
            ],
        },
        {
            "name": "service",
            "description": "Service offered by the company",
            "attributes": [
                {"name": "service_name", "type": "text"},
                {"name": "service_type", "type": "text"},
                {"name": "components_serviced", "type": "text"},
            ],
        },
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
    },
    "is_template": True,
}

# Simplified version focusing on key fields for faster extraction
DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE = {
    "name": "drivetrain_company_simple",
    "description": "Simplified extraction for drivetrain component companies - key facts only",
    "source_config": {"type": "web", "group_by": "company"},
    "extraction_context": {
        "source_type": "company documentation",
        "source_label": "Company",
        "entity_id_fields": ["product_name", "entity_id", "name", "id"],
    },
    "extraction_schema": {
        "name": "company_summary",
        "version": "1.0",
        "field_groups": [
            {
                "name": "company_summary",
                "description": "Simplified company summary for drivetrain companies",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "company_name",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Company name",
                    },
                    {
                        "name": "business_type",
                        "field_type": "enum",
                        "required": True,
                        "default": "unknown",
                        "enum_values": [
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
                        "field_type": "list",
                        "required": False,
                        "description": "List of product categories: gearboxes, motors, drivetrain_accessories",
                    },
                    {
                        "name": "service_categories",
                        "field_type": "list",
                        "required": False,
                        "description": "List of serviced categories: gearboxes, motors, drivetrain_accessories",
                    },
                    {
                        "name": "employee_count",
                        "field_type": "text",
                        "required": False,
                        "description": "Number of employees or range",
                    },
                    {
                        "name": "site_count",
                        "field_type": "integer",
                        "required": False,
                        "description": "Number of locations",
                    },
                    {
                        "name": "site_locations",
                        "field_type": "list",
                        "required": False,
                        "description": "List of site locations (city, country)",
                    },
                    {
                        "name": "confidence",
                        "field_type": "float",
                        "required": True,
                        "default": 0.8,
                        "description": "Confidence score",
                    },
                ],
            },
            {
                "name": "products_list",
                "description": "List of products with specifications",
                "is_entity_list": True,
                "fields": [
                    {
                        "name": "product_name",
                        "field_type": "text",
                        "required": True,
                        "default": "",
                        "description": "Product name",
                    },
                    {
                        "name": "category",
                        "field_type": "text",
                        "required": False,
                        "description": "Product category",
                    },
                    {
                        "name": "power_kw",
                        "field_type": "float",
                        "required": False,
                        "description": "Power in kW",
                    },
                    {
                        "name": "torque_nm",
                        "field_type": "float",
                        "required": False,
                        "description": "Torque in Nm",
                    },
                    {
                        "name": "speed_rpm",
                        "field_type": "float",
                        "required": False,
                        "description": "Speed in RPM",
                    },
                ],
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

# Default template for projects without custom schema
DEFAULT_EXTRACTION_TEMPLATE = {
    "name": "default",
    "description": "Generic extraction template for any content type",
    "source_config": {"type": "web", "group_by": "source"},
    "extraction_context": {
        "source_type": "content",
        "source_label": "Source",
        "entity_id_fields": ["product_name", "entity_id", "name", "id"],
    },
    "extraction_schema": {
        "name": "generic_facts",
        "version": "1.0",
        "description": "Generic fact extraction schema",
        "field_groups": [
            {
                "name": "entity_info",
                "description": "Basic entity identification",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "entity_name",
                        "field_type": "text",
                        "description": "Name of the primary entity or subject",
                        "required": True,
                        "default": "",
                    },
                    {
                        "name": "entity_type",
                        "field_type": "enum",
                        "description": "Type of entity",
                        "required": True,
                        "default": "unknown",
                        "enum_values": ["company", "product", "person", "organization", "location", "unknown"],
                    },
                    {
                        "name": "description",
                        "field_type": "text",
                        "description": "Brief description of the entity",
                        "required": False,
                    },
                ],
            },
            {
                "name": "key_facts",
                "description": "Important factual information",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "fact_category",
                        "field_type": "enum",
                        "description": "Category of fact",
                        "required": True,
                        "default": "general",
                        "enum_values": ["general", "technical", "financial", "operational", "historical"],
                    },
                    {
                        "name": "fact_text",
                        "field_type": "text",
                        "description": "The factual statement",
                        "required": True,
                        "default": "",
                    },
                    {
                        "name": "confidence",
                        "field_type": "float",
                        "description": "Confidence score 0.0-1.0",
                        "required": True,
                        "default": 0.8,
                    },
                ],
            },
            {
                "name": "contact_info",
                "description": "Contact and location information",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "locations",
                        "field_type": "list",
                        "description": "List of locations (city, country)",
                        "required": False,
                    },
                    {
                        "name": "website",
                        "field_type": "text",
                        "description": "Website URL",
                        "required": False,
                    },
                    {
                        "name": "contact_email",
                        "field_type": "text",
                        "description": "Contact email address",
                        "required": False,
                    },
                ],
            },
        ],
    },
    "entity_types": [
        {"name": "entity", "description": "Generic named entity"},
        {"name": "fact", "description": "Factual statement"},
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
    "DEFAULT_EXTRACTION_TEMPLATE",
]
