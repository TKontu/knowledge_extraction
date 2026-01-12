"""Tests for deployment configuration."""

from pathlib import Path

import pytest
import yaml


class TestDockerCompose:
    @pytest.fixture
    def compose_config(self) -> dict:
        """Load docker-compose.yml."""
        compose_path = Path(__file__).parent.parent / "docker-compose.yml"
        with open(compose_path) as f:
            return yaml.safe_load(f)

    def test_all_services_have_resource_limits(self, compose_config):
        """All services should have resource limits defined."""
        services = compose_config.get("services", {})

        # Services that must have limits
        required_services = ["pipeline", "postgres", "redis", "qdrant"]

        for service_name in required_services:
            service = services.get(service_name, {})
            deploy = service.get("deploy", {})
            resources = deploy.get("resources", {})
            limits = resources.get("limits", {})

            assert "memory" in limits, f"{service_name} missing memory limit"

    def test_migrate_service_exists(self, compose_config):
        """Migration service should be configured."""
        services = compose_config.get("services", {})
        assert "migrate" in services

        migrate = services["migrate"]
        assert "alembic" in migrate.get("command", "")

    def test_pipeline_depends_on_migrate(self, compose_config):
        """Pipeline should wait for migrations."""
        services = compose_config.get("services", {})
        pipeline = services.get("pipeline", {})
        depends = pipeline.get("depends_on", {})

        assert "migrate" in depends


class TestLegacyFiles:
    def test_init_sql_removed(self):
        """init.sql should be removed (replaced by Alembic migrations)."""
        init_sql = Path(__file__).parent.parent / "init.sql"
        assert not init_sql.exists(), "init.sql should be deleted"


class TestDeploymentDocs:
    def test_deployment_docs_exist(self):
        """Deployment documentation should exist."""
        docs_path = Path(__file__).parent.parent / "docs" / "DEPLOYMENT.md"
        assert docs_path.exists(), "docs/DEPLOYMENT.md should exist"

    def test_deployment_docs_has_required_sections(self):
        """Deployment docs should have key sections."""
        docs_path = Path(__file__).parent.parent / "docs" / "DEPLOYMENT.md"
        content = docs_path.read_text()

        required_sections = [
            "Prerequisites",
            "Quick Start",
            "Environment Variables",
            "Migrations",
            "Backup",
        ]

        for section in required_sections:
            assert section in content, f"Missing section: {section}"
