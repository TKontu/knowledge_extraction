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

__all__ = [
    "COMPANY_ANALYSIS_TEMPLATE",
    "RESEARCH_SURVEY_TEMPLATE",
    "CONTRACT_REVIEW_TEMPLATE",
    "BOOK_CATALOG_TEMPLATE",
]
