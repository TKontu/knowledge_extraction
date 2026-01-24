"""Tests for template loader module."""

from pathlib import Path

import pytest

from services.extraction.schema_adapter import SchemaAdapter
from services.projects.template_loader import (
    TemplateLoadError,
    TemplateRegistry,
)


class TestTemplateRegistry:
    """Test TemplateRegistry class."""

    def test_load_valid_templates(self, tmp_path):
        """Create valid YAML, load, verify it's in registry."""
        # Create a valid template YAML
        template_yaml = """name: test_template
description: Test template
source_config:
  type: web
  group_by: category
extraction_context:
  source_type: test source
  source_label: Test
  entity_id_fields:
    - entity_id
    - name
extraction_schema:
  name: test_schema
  version: "1.0"
  field_groups:
    - name: test_group
      description: Test group
      is_entity_list: false
      fields:
        - name: test_field
          field_type: text
          description: Test field
          required: true
          default: ""
entity_types:
  - name: test_entity
    description: Test entity
prompt_templates: {}
is_template: true
"""
        template_file = tmp_path / "test_template.yaml"
        template_file.write_text(template_yaml)

        # Load templates
        registry = TemplateRegistry()
        registry.load_templates(tmp_path)

        # Verify template is in registry
        template = registry.get("test_template")
        assert template is not None
        assert template["name"] == "test_template"
        assert template["description"] == "Test template"
        assert "extraction_schema" in template

    def test_load_invalid_schema_fails(self, tmp_path):
        """Create YAML with missing field_groups, verify TemplateLoadError raised."""
        # Create invalid template YAML (missing field_groups)
        invalid_yaml = """name: invalid_template
description: Invalid template
source_config:
  type: web
extraction_context:
  source_type: test
  source_label: Test
extraction_schema:
  name: invalid_schema
  version: "1.0"
entity_types: []
prompt_templates: {}
is_template: true
"""
        template_file = tmp_path / "invalid_template.yaml"
        template_file.write_text(invalid_yaml)

        # Attempt to load should raise TemplateLoadError
        registry = TemplateRegistry()
        with pytest.raises(TemplateLoadError) as exc_info:
            registry.load_templates(tmp_path)

        # Verify error has template_name and errors
        assert exc_info.value.template_name == "invalid_template"
        assert len(exc_info.value.errors) > 0
        assert any("field_groups" in error for error in exc_info.value.errors)

    def test_get_nonexistent_returns_none(self):
        """Verify get() returns None for unknown template name."""
        registry = TemplateRegistry()
        template = registry.get("nonexistent_template")
        assert template is None

    def test_list_names_returns_all_templates(self, tmp_path):
        """Load multiple templates, verify list_names() returns all."""
        # Create two valid templates
        template1_yaml = """name: template_one
description: Template One
source_config:
  type: web
extraction_context:
  source_type: test
  source_label: Test
extraction_schema:
  name: schema_one
  version: "1.0"
  field_groups:
    - name: group_one
      description: Group One
      is_entity_list: false
      fields:
        - name: field_one
          field_type: text
          description: Field One
          required: true
          default: ""
entity_types: []
prompt_templates: {}
is_template: true
"""
        template2_yaml = """name: template_two
description: Template Two
source_config:
  type: web
extraction_context:
  source_type: test
  source_label: Test
extraction_schema:
  name: schema_two
  version: "1.0"
  field_groups:
    - name: group_two
      description: Group Two
      is_entity_list: false
      fields:
        - name: field_two
          field_type: text
          description: Field Two
          required: true
          default: ""
entity_types: []
prompt_templates: {}
is_template: true
"""
        (tmp_path / "template_one.yaml").write_text(template1_yaml)
        (tmp_path / "template_two.yaml").write_text(template2_yaml)

        # Load templates
        registry = TemplateRegistry()
        registry.load_templates(tmp_path)

        # Verify list_names returns both
        names = registry.list_names()
        assert len(names) == 2
        assert "template_one" in names
        assert "template_two" in names


class TestBackwardCompatibility:
    """Test backward compatibility with old import style."""

    def test_import_company_analysis_template(self):
        """from services.projects.templates import COMPANY_ANALYSIS_TEMPLATE works."""
        from services.projects.templates import COMPANY_ANALYSIS_TEMPLATE

        assert COMPANY_ANALYSIS_TEMPLATE is not None
        assert COMPANY_ANALYSIS_TEMPLATE["name"] == "company_analysis"
        assert "extraction_schema" in COMPANY_ANALYSIS_TEMPLATE

    def test_import_default_template(self):
        """from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE works."""
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

        assert DEFAULT_EXTRACTION_TEMPLATE is not None
        assert DEFAULT_EXTRACTION_TEMPLATE["name"] == "default"
        assert "extraction_schema" in DEFAULT_EXTRACTION_TEMPLATE

    def test_all_templates_importable(self):
        """All 7 template constants can be imported."""
        from services.projects.templates import (
            BOOK_CATALOG_TEMPLATE,
            COMPANY_ANALYSIS_TEMPLATE,
            CONTRACT_REVIEW_TEMPLATE,
            DEFAULT_EXTRACTION_TEMPLATE,
            DRIVETRAIN_COMPANY_TEMPLATE,
            DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE,
            RESEARCH_SURVEY_TEMPLATE,
        )

        templates = [
            COMPANY_ANALYSIS_TEMPLATE,
            RESEARCH_SURVEY_TEMPLATE,
            CONTRACT_REVIEW_TEMPLATE,
            BOOK_CATALOG_TEMPLATE,
            DRIVETRAIN_COMPANY_TEMPLATE,
            DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE,
            DEFAULT_EXTRACTION_TEMPLATE,
        ]

        # Verify all are non-None
        for template in templates:
            assert template is not None
            assert "name" in template
            assert "extraction_schema" in template


class TestProductionTemplates:
    """Test production templates in templates/ directory."""

    def test_all_yaml_files_load(self):
        """All 7 YAML files in templates/ directory load successfully."""
        from services.projects.template_loader import _registry

        # Force load production templates
        templates_dir = Path(__file__).parent.parent / "src" / "services" / "projects" / "templates"
        _registry.load_templates(templates_dir)

        # Verify all 7 templates loaded
        names = _registry.list_names()
        assert len(names) == 7

        expected_names = [
            "company_analysis",
            "research_survey",
            "contract_review",
            "book_catalog",
            "drivetrain_company_analysis",
            "drivetrain_company_simple",
            "default",
        ]

        for expected_name in expected_names:
            assert expected_name in names, f"Template {expected_name} not loaded"

    def test_all_yaml_files_validate(self):
        """All loaded templates pass SchemaAdapter validation."""
        from services.projects.template_loader import _registry

        # Force load production templates
        templates_dir = Path(__file__).parent.parent / "src" / "services" / "projects" / "templates"
        _registry.load_templates(templates_dir)

        adapter = SchemaAdapter()

        # Validate each template
        for name in _registry.list_names():
            template = _registry.get(name)
            assert template is not None, f"Template {name} is None"

            schema = template.get("extraction_schema")
            assert schema is not None, f"Template {name} has no extraction_schema"

            result = adapter.validate_extraction_schema(schema)
            assert result.is_valid, f"Template {name} failed validation: {result.errors}"

    def test_template_names_match_filenames(self):
        """Template 'name' field matches the YAML filename (without .yaml)."""
        templates_dir = Path(__file__).parent.parent / "src" / "services" / "projects" / "templates"

        if not templates_dir.exists():
            pytest.skip("Templates directory does not exist yet")

        import yaml

        for yaml_file in templates_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                template = yaml.safe_load(f)

            filename_stem = yaml_file.stem  # filename without .yaml
            template_name = template.get("name")

            # For drivetrain templates, handle the naming difference
            if filename_stem == "drivetrain_company":
                assert template_name == "drivetrain_company_analysis"
            else:
                assert template_name == filename_stem, (
                    f"Template name '{template_name}' does not match "
                    f"filename '{filename_stem}' in {yaml_file}"
                )
