"""Repository for Project CRUD operations."""

from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import select
from orm_models import Project
from .templates import COMPANY_ANALYSIS_TEMPLATE


class ProjectRepository:
    """Repository for managing Project entities."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self._session = session

    async def create(
        self,
        name: str,
        extraction_schema: dict,
        description: Optional[str] = None,
        source_config: Optional[dict] = None,
        entity_types: Optional[list] = None,
        prompt_templates: Optional[dict] = None,
        is_template: bool = False,
    ) -> Project:
        """Create a new project.

        Args:
            name: Unique project name
            extraction_schema: JSONB schema defining extraction fields
            description: Optional project description
            source_config: Optional source configuration
            entity_types: Optional list of entity type definitions
            prompt_templates: Optional custom prompt templates
            is_template: Whether this is a template project

        Returns:
            Created Project instance
        """
        project = Project(
            name=name,
            description=description,
            extraction_schema=extraction_schema,
        )

        if source_config is not None:
            project.source_config = source_config

        if entity_types is not None:
            project.entity_types = entity_types

        if prompt_templates is not None:
            project.prompt_templates = prompt_templates

        project.is_template = is_template

        self._session.add(project)
        self._session.flush()
        return project

    async def get(self, project_id: UUID) -> Optional[Project]:
        """Get project by ID.

        Args:
            project_id: UUID of the project

        Returns:
            Project if found, None otherwise
        """
        result = self._session.execute(
            select(Project).where(Project.id == project_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[Project]:
        """Get project by name.

        Args:
            name: Project name

        Returns:
            Project if found, None otherwise
        """
        result = self._session.execute(select(Project).where(Project.name == name))
        return result.scalar_one_or_none()

    async def list_all(self, include_inactive: bool = False) -> list[Project]:
        """List all projects.

        Args:
            include_inactive: Whether to include inactive projects

        Returns:
            List of projects sorted by name
        """
        query = select(Project)
        if not include_inactive:
            query = query.where(Project.is_active == True)
        query = query.order_by(Project.name)

        result = self._session.execute(query)
        return list(result.scalars().all())

    async def list_templates(self) -> list[Project]:
        """List template projects.

        Returns:
            List of active template projects sorted by name
        """
        result = self._session.execute(
            select(Project)
            .where(Project.is_template == True)
            .where(Project.is_active == True)
            .order_by(Project.name)
        )
        return list(result.scalars().all())

    async def update(self, project_id: UUID, updates: dict) -> Optional[Project]:
        """Update project fields.

        Args:
            project_id: UUID of the project to update
            updates: Dictionary of field names and new values

        Returns:
            Updated Project if found, None otherwise
        """
        project = await self.get(project_id)
        if not project:
            return None

        for key, value in updates.items():
            if hasattr(project, key):
                setattr(project, key, value)

        self._session.flush()
        return project

    async def delete(self, project_id: UUID) -> bool:
        """Soft delete a project by setting is_active=False.

        Args:
            project_id: UUID of the project to delete

        Returns:
            True if project was deleted, False if not found
        """
        project = await self.get(project_id)
        if not project:
            return False

        project.is_active = False
        self._session.flush()
        return True

    async def get_default_project(self) -> Project:
        """Get or create the default company_analysis project.

        Returns:
            The default company_analysis Project
        """
        project = await self.get_by_name("company_analysis")
        if project:
            return project

        # Create default project from template
        return await self.create(
            name=COMPANY_ANALYSIS_TEMPLATE["name"],
            description=COMPANY_ANALYSIS_TEMPLATE["description"],
            source_config=COMPANY_ANALYSIS_TEMPLATE["source_config"],
            extraction_schema=COMPANY_ANALYSIS_TEMPLATE["extraction_schema"],
            entity_types=COMPANY_ANALYSIS_TEMPLATE["entity_types"],
            prompt_templates=COMPANY_ANALYSIS_TEMPLATE["prompt_templates"],
            is_template=COMPANY_ANALYSIS_TEMPLATE["is_template"],
        )
