"""Tests for project schema update safety (force parameter)."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import status
from fastapi.testclient import TestClient


class TestProjectSchemaUpdateSafety:
    """Tests for schema update safety when extractions exist."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = MagicMock()
        return db

    @pytest.fixture
    def mock_project(self):
        """Create a mock project with all required attributes."""
        project = MagicMock()
        project.id = uuid4()
        project.name = "Test Project"
        project.description = "Test description"
        project.extraction_schema = {"name": "test", "field_groups": []}
        project.entity_types = ["product", "feature"]
        project.source_config = {}
        project.prompt_templates = {}
        project.is_template = False
        project.is_active = True
        project.created_at = datetime.utcnow()
        project.updated_at = datetime.utcnow()
        return project

    @pytest.fixture
    def client(self):
        """Create test client."""
        # Import here to avoid circular imports
        import sys
        sys.path.insert(0, "src")
        from main import app
        return TestClient(app)

    def test_update_schema_blocked_without_force(self, mock_db, mock_project):
        """Test that schema updates are blocked when extractions exist."""
        from api.v1.projects import update_project
        from models import ProjectUpdate
        from fastapi import Response, HTTPException

        project_id = mock_project.id

        # Mock repository to return existing project
        with patch("api.v1.projects.ProjectRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get.return_value = mock_project
            MockRepo.return_value = mock_repo

            # Mock extraction count query to return > 0
            mock_scalar_result = MagicMock()
            mock_scalar_result.scalar.return_value = 100
            mock_db.execute.return_value = mock_scalar_result

            # Create update with schema change
            project_update = ProjectUpdate(
                extraction_schema={"name": "new_schema", "field_groups": []}
            )
            response = Response()

            # Should raise 409 Conflict
            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.get_event_loop().run_until_complete(
                    update_project(
                        project_id=project_id,
                        project_update=project_update,
                        response=response,
                        force=False,
                        db=mock_db,
                    )
                )

            assert exc_info.value.status_code == status.HTTP_409_CONFLICT
            assert "100 existing extractions" in str(exc_info.value.detail)
            assert "force=true" in str(exc_info.value.detail)

    def test_update_schema_allowed_with_force(self, mock_db, mock_project):
        """Test that schema updates proceed when force=True."""
        from api.v1.projects import update_project
        from models import ProjectUpdate
        from fastapi import Response

        project_id = mock_project.id

        # Mock repository
        with patch("api.v1.projects.ProjectRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get.return_value = mock_project
            mock_repo.update.return_value = mock_project
            MockRepo.return_value = mock_repo

            # Mock extraction count query
            mock_scalar_result = MagicMock()
            mock_scalar_result.scalar.return_value = 100
            mock_db.execute.return_value = mock_scalar_result

            # Create update with schema change
            project_update = ProjectUpdate(
                extraction_schema={"name": "new_schema", "field_groups": []}
            )
            response = Response()

            # Should succeed with force=True
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                update_project(
                    project_id=project_id,
                    project_update=project_update,
                    response=response,
                    force=True,
                    db=mock_db,
                )
            )

            # Verify warning header is set
            assert "X-Extraction-Warning" in response.headers
            assert "100 existing extractions" in response.headers["X-Extraction-Warning"]

    def test_update_schema_no_extractions_no_block(self, mock_db, mock_project):
        """Test that schema updates proceed freely when no extractions exist."""
        from api.v1.projects import update_project
        from models import ProjectUpdate
        from fastapi import Response

        project_id = mock_project.id

        # Mock repository
        with patch("api.v1.projects.ProjectRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get.return_value = mock_project
            mock_repo.update.return_value = mock_project
            MockRepo.return_value = mock_repo

            # Mock extraction count = 0
            mock_scalar_result = MagicMock()
            mock_scalar_result.scalar.return_value = 0
            mock_db.execute.return_value = mock_scalar_result

            # Create update with schema change
            project_update = ProjectUpdate(
                extraction_schema={"name": "new_schema", "field_groups": []}
            )
            response = Response()

            # Should succeed without force
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                update_project(
                    project_id=project_id,
                    project_update=project_update,
                    response=response,
                    force=False,
                    db=mock_db,
                )
            )

            # No warning header when no extractions
            assert "X-Extraction-Warning" not in response.headers

    def test_update_non_schema_fields_not_blocked(self, mock_db, mock_project):
        """Test that non-schema updates are not blocked."""
        from api.v1.projects import update_project
        from models import ProjectUpdate
        from fastapi import Response

        project_id = mock_project.id

        # Mock repository
        with patch("api.v1.projects.ProjectRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get.return_value = mock_project
            mock_repo.update.return_value = mock_project
            MockRepo.return_value = mock_repo

            # Create update without schema change (just name)
            project_update = ProjectUpdate(name="New Name")
            response = Response()

            # Should succeed without checking extractions
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                update_project(
                    project_id=project_id,
                    project_update=project_update,
                    response=response,
                    force=False,
                    db=mock_db,
                )
            )

            # No extraction count query should be made for non-schema updates
            # (the query would only be made if schema_changed or entities_changed)

    def test_update_entity_types_blocked(self, mock_db, mock_project):
        """Test that entity_types updates are also blocked."""
        from api.v1.projects import update_project
        from models import ProjectUpdate
        from fastapi import Response, HTTPException

        project_id = mock_project.id

        with patch("api.v1.projects.ProjectRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get.return_value = mock_project
            MockRepo.return_value = mock_repo

            # Mock extraction count > 0
            mock_scalar_result = MagicMock()
            mock_scalar_result.scalar.return_value = 50
            mock_db.execute.return_value = mock_scalar_result

            # Update entity_types
            project_update = ProjectUpdate(
                entity_types=["new_type_1", "new_type_2"]
            )
            response = Response()

            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.get_event_loop().run_until_complete(
                    update_project(
                        project_id=project_id,
                        project_update=project_update,
                        response=response,
                        force=False,
                        db=mock_db,
                    )
                )

            assert exc_info.value.status_code == status.HTTP_409_CONFLICT
            assert "entity_types" in str(exc_info.value.detail)

    def test_error_detail_structure(self, mock_db, mock_project):
        """Test that error detail has expected structure."""
        from api.v1.projects import update_project
        from models import ProjectUpdate
        from fastapi import Response, HTTPException

        project_id = mock_project.id

        with patch("api.v1.projects.ProjectRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get.return_value = mock_project
            MockRepo.return_value = mock_repo

            mock_scalar_result = MagicMock()
            mock_scalar_result.scalar.return_value = 25
            mock_db.execute.return_value = mock_scalar_result

            project_update = ProjectUpdate(
                extraction_schema={"name": "new"},
                entity_types=["type1"],
            )
            response = Response()

            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.get_event_loop().run_until_complete(
                    update_project(
                        project_id=project_id,
                        project_update=project_update,
                        response=response,
                        force=False,
                        db=mock_db,
                    )
                )

            detail = exc_info.value.detail
            assert detail["error"] == "Schema modification blocked"
            assert detail["extraction_count"] == 25
            assert "extraction_schema" in detail["message"]
            assert "entity_types" in detail["message"]
            assert "force=true" in detail["resolution"]
