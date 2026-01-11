import pytest

from src.services.projects.templates import (
    COMPANY_ANALYSIS_TEMPLATE,
    CONTRACT_REVIEW_TEMPLATE,
    RESEARCH_SURVEY_TEMPLATE,
)


class TestProjectTemplates:
    def test_research_survey_template_has_required_fields(self):
        assert RESEARCH_SURVEY_TEMPLATE["name"] == "research_survey"
        assert RESEARCH_SURVEY_TEMPLATE["is_template"] is True
        assert "extraction_schema" in RESEARCH_SURVEY_TEMPLATE
        assert "entity_types" in RESEARCH_SURVEY_TEMPLATE

    def test_research_survey_entity_types(self):
        entity_names = [e["name"] for e in RESEARCH_SURVEY_TEMPLATE["entity_types"]]
        assert "author" in entity_names
        assert "method" in entity_names
        assert "dataset" in entity_names

    def test_contract_review_template_has_required_fields(self):
        assert CONTRACT_REVIEW_TEMPLATE["name"] == "contract_review"
        assert CONTRACT_REVIEW_TEMPLATE["is_template"] is True
        assert "extraction_schema" in CONTRACT_REVIEW_TEMPLATE
        assert "entity_types" in CONTRACT_REVIEW_TEMPLATE

    def test_contract_review_entity_types(self):
        entity_names = [e["name"] for e in CONTRACT_REVIEW_TEMPLATE["entity_types"]]
        assert "party" in entity_names
        assert "date" in entity_names
        assert "amount" in entity_names

    def test_all_templates_have_consistent_structure(self):
        templates = [
            COMPANY_ANALYSIS_TEMPLATE,
            RESEARCH_SURVEY_TEMPLATE,
            CONTRACT_REVIEW_TEMPLATE,
        ]
        required_keys = [
            "name",
            "description",
            "source_config",
            "extraction_schema",
            "entity_types",
            "is_template",
        ]
        for template in templates:
            for key in required_keys:
                assert key in template, f"Template {template['name']} missing {key}"
