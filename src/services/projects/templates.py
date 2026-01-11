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
