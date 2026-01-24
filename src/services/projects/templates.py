"""Project templates for common extraction use cases.

All templates use the new schema format with:
- field_groups[] wrapper
- field_type (not type)
- enum_values (not values)
"""


def __getattr__(name: str):
    """Lazy load templates from YAML files for backward compatibility."""
    template_mapping = {
        "COMPANY_ANALYSIS_TEMPLATE": "company_analysis",
        "RESEARCH_SURVEY_TEMPLATE": "research_survey",
        "CONTRACT_REVIEW_TEMPLATE": "contract_review",
        "BOOK_CATALOG_TEMPLATE": "book_catalog",
        "DRIVETRAIN_COMPANY_TEMPLATE": "drivetrain_company_analysis",
        "DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE": "drivetrain_company_simple",
        "DEFAULT_EXTRACTION_TEMPLATE": "default",
    }
    if name in template_mapping:
        from services.projects.template_loader import get_template

        template = get_template(template_mapping[name])
        if template is None:
            raise AttributeError(f"Template not found: {name}")
        return template
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [  # noqa: F822
    "COMPANY_ANALYSIS_TEMPLATE",
    "RESEARCH_SURVEY_TEMPLATE",
    "CONTRACT_REVIEW_TEMPLATE",
    "BOOK_CATALOG_TEMPLATE",
    "DRIVETRAIN_COMPANY_TEMPLATE",
    "DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE",
    "DEFAULT_EXTRACTION_TEMPLATE",
]
