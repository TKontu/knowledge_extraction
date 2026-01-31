"""Template loader module for loading templates from YAML files."""

from pathlib import Path
from typing import Any

import structlog
import yaml

from services.extraction.schema_adapter import (
    ClassificationConfig,
    CrawlConfig,
    SchemaAdapter,
)

logger = structlog.get_logger(__name__)


class TemplateLoadError(Exception):
    """Exception raised when template loading fails."""

    def __init__(self, template_name: str, errors: list[str]) -> None:
        """Initialize TemplateLoadError.

        Args:
            template_name: Name of the template that failed to load.
            errors: List of validation errors.
        """
        self.template_name = template_name
        self.errors = errors
        error_msg = f"Failed to load template '{template_name}': {', '.join(errors)}"
        super().__init__(error_msg)


class TemplateRegistry:
    """Registry for loading and managing templates."""

    def __init__(self) -> None:
        """Initialize empty template registry."""
        self._templates: dict[str, dict[str, Any]] = {}
        self._loaded: bool = False
        self._adapter = SchemaAdapter()

    def load_templates(self, templates_dir: Path | None = None) -> None:
        """Load all YAML templates from directory.

        Args:
            templates_dir: Directory containing template YAML files.
                          Defaults to templates/ subdirectory.

        Raises:
            TemplateLoadError: If any template fails validation.
        """
        if templates_dir is None:
            templates_dir = Path(__file__).parent / "templates"

        # Convert to Path if string
        if isinstance(templates_dir, str):
            templates_dir = Path(templates_dir)

        if not templates_dir.exists():
            logger.warning("templates_directory_not_found", path=str(templates_dir))
            return

        # Load all YAML files
        yaml_files = list(templates_dir.glob("*.yaml"))
        logger.info("loading_templates", directory=str(templates_dir), count=len(yaml_files))

        for yaml_file in yaml_files:
            template = self._load_and_validate(yaml_file)
            template_name = template["name"]
            self._templates[template_name] = template
            logger.debug("template_loaded", name=template_name, file=yaml_file.name)

        self._loaded = True
        logger.info("templates_loaded", count=len(self._templates))

    def _load_and_validate(self, yaml_file: Path) -> dict[str, Any]:
        """Load single template file and validate.

        Args:
            yaml_file: Path to YAML template file.

        Returns:
            Validated template dictionary.

        Raises:
            TemplateLoadError: If template fails validation.
        """
        # Load YAML
        try:
            with open(yaml_file) as f:
                template = yaml.safe_load(f)
        except Exception as e:
            raise TemplateLoadError(
                yaml_file.stem, [f"Failed to parse YAML: {str(e)}"]
            ) from e

        if not isinstance(template, dict):
            raise TemplateLoadError(
                yaml_file.stem, ["Template must be a dictionary"]
            )

        template_name = template.get("name", yaml_file.stem)

        # Validate extraction_schema
        if "extraction_schema" not in template:
            raise TemplateLoadError(
                template_name, ["Template missing 'extraction_schema' field"]
            )

        schema = template["extraction_schema"]
        result = self._adapter.validate_extraction_schema(schema)

        # Log warnings but don't fail
        for warning in result.warnings:
            logger.warning("template_validation_warning", template=template_name, warning=warning)

        # Fail on errors
        if not result.is_valid:
            raise TemplateLoadError(template_name, result.errors)

        # Validate classification_config if present
        if "classification_config" in template:
            classification_config = ClassificationConfig.from_dict(
                template["classification_config"]
            )
            is_valid, config_errors = classification_config.validate()
            if not is_valid:
                raise TemplateLoadError(template_name, config_errors)

        # Validate crawl_config if present
        if "crawl_config" in template:
            crawl_config = CrawlConfig.from_dict(template["crawl_config"])
            if crawl_config:
                is_valid, config_errors = crawl_config.validate()
                if not is_valid:
                    raise TemplateLoadError(template_name, config_errors)

        return template

    def get(self, name: str) -> dict[str, Any] | None:
        """Get template by name.

        Args:
            name: Template name.

        Returns:
            Template dictionary or None if not found.
        """
        # Lazy load if not yet loaded
        if not self._loaded:
            self.load_templates()

        return self._templates.get(name)

    def get_all(self) -> dict[str, dict[str, Any]]:
        """Get all templates.

        Returns:
            Dictionary mapping template names to template dictionaries (copy).
        """
        # Lazy load if not yet loaded
        if not self._loaded:
            self.load_templates()

        return self._templates.copy()

    def list_names(self) -> list[str]:
        """List all template names.

        Returns:
            List of template names.
        """
        # Lazy load if not yet loaded
        if not self._loaded:
            self.load_templates()

        return list(self._templates.keys())


# Global registry instance
_registry = TemplateRegistry()


# Module-level functions that delegate to global registry
def get_template(name: str) -> dict[str, Any] | None:
    """Get template by name from global registry.

    Args:
        name: Template name.

    Returns:
        Template dictionary or None if not found.
    """
    return _registry.get(name)


def get_all_templates() -> dict[str, dict[str, Any]]:
    """Get all templates from global registry.

    Returns:
        Dictionary mapping template names to template dictionaries.
    """
    return _registry.get_all()


def list_template_names() -> list[str]:
    """List all template names from global registry.

    Returns:
        List of template names.
    """
    return _registry.list_names()


def load_templates(templates_dir: Path | None = None) -> None:
    """Load templates from directory into global registry.

    Args:
        templates_dir: Directory containing template YAML files.
                      Defaults to templates/ subdirectory.

    Raises:
        TemplateLoadError: If any template fails validation.
    """
    _registry.load_templates(templates_dir)
