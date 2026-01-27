"""Tests for generalized ORM models (Project, Source, Extraction, Entity)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from database import engine
from orm_models import Entity, Extraction, ExtractionEntity, Project, Source


@pytest.fixture
def db_session():
    """Create a fresh database session for each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


class TestProjectModel:
    """Test Project ORM model."""

    def test_create_project_with_minimal_fields(self, db_session):
        """Should create project with only required fields."""
        project = Project(
            name="test_project",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(project)
        db_session.commit()

        assert project.id is not None
        assert project.name == "test_project"
        assert project.extraction_schema == {"name": "test", "fields": []}
        assert project.source_config == {"type": "web", "group_by": "company"}
        assert project.entity_types == []
        assert project.prompt_templates == {}
        assert project.is_template is False
        assert project.is_active is True
        assert isinstance(project.created_at, datetime)
        assert isinstance(project.updated_at, datetime)

    def test_create_project_with_all_fields(self, db_session):
        """Should create project with all fields populated."""
        project = Project(
            name="full_project",
            description="A complete project",
            source_config={"type": "pdf", "group_by": "document"},
            extraction_schema={
                "name": "finding",
                "fields": [{"name": "text", "type": "text", "required": True}],
            },
            entity_types=[{"name": "author", "description": "Paper author"}],
            prompt_templates={"extraction": "Custom prompt"},
            is_template=True,
            is_active=False,
        )
        db_session.add(project)
        db_session.commit()

        assert project.name == "full_project"
        assert project.description == "A complete project"
        assert project.source_config["type"] == "pdf"
        assert project.extraction_schema["name"] == "finding"
        assert len(project.entity_types) == 1
        assert project.prompt_templates["extraction"] == "Custom prompt"
        assert project.is_template is True
        assert project.is_active is False

    def test_project_name_must_be_unique(self, db_session):
        """Should enforce unique constraint on project name."""
        project1 = Project(
            name="duplicate",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(project1)
        db_session.commit()

        project2 = Project(
            name="duplicate",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(project2)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()

    def test_project_has_relationships(self, db_session):
        """Should have relationships to sources and extractions."""
        project = Project(
            name="relationship_test",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(project)
        db_session.commit()

        # Check relationships exist (even if empty)
        assert hasattr(project, "sources")
        assert hasattr(project, "extractions")
        assert project.sources == []
        assert project.extractions == []


class TestSourceModel:
    """Test Source ORM model."""

    def test_create_source_with_minimal_fields(self, db_session):
        """Should create source with required fields."""
        project = Project(
            name="source_test_project",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            uri="https://example.com/doc",
            source_group="Example Corp",
        )
        db_session.add(source)
        db_session.commit()

        assert source.id is not None
        assert source.project_id == project.id
        assert source.source_type == "web"
        assert source.uri == "https://example.com/doc"
        assert source.source_group == "Example Corp"
        assert source.status == "pending"
        assert source.meta_data == {}
        assert source.outbound_links == []
        assert isinstance(source.created_at, datetime)

    def test_create_source_with_all_fields(self, db_session):
        """Should create source with all fields populated."""
        project = Project(
            name="source_full_test",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            source_type="pdf",
            uri="/path/to/document.pdf",
            source_group="Research Papers",
            title="Important Paper",
            content="Processed markdown content",
            raw_content="<html>Raw content</html>",
            meta_data={"author": "John Doe", "year": 2024},
            outbound_links=["https://ref1.com", "https://ref2.com"],
            status="completed",
            fetched_at=datetime.now(UTC),
        )
        db_session.add(source)
        db_session.commit()

        assert source.source_type == "pdf"
        assert source.title == "Important Paper"
        assert source.content == "Processed markdown content"
        assert source.raw_content == "<html>Raw content</html>"
        assert source.meta_data["author"] == "John Doe"
        assert len(source.outbound_links) == 2
        assert source.status == "completed"

    def test_source_project_uri_must_be_unique(self, db_session):
        """Should enforce unique constraint on (project_id, uri)."""
        project = Project(
            name="unique_test",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source1 = Source(
            project_id=project.id,
            uri="https://example.com/same",
            source_group="Group A",
        )
        db_session.add(source1)
        db_session.commit()

        source2 = Source(
            project_id=project.id,
            uri="https://example.com/same",
            source_group="Group B",
        )
        db_session.add(source2)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()

    def test_source_has_relationship_to_project(self, db_session):
        """Should have relationship to project."""
        project = Project(
            name="rel_test",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            uri="https://example.com",
            source_group="Test",
        )
        db_session.add(source)
        db_session.commit()

        # Refresh to load relationships
        db_session.refresh(source)
        db_session.refresh(project)

        assert source.project == project
        assert source in project.sources


class TestExtractionModel:
    """Test Extraction ORM model."""

    def test_create_extraction_with_minimal_fields(self, db_session):
        """Should create extraction with required fields."""
        project = Project(
            name="extraction_test",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            uri="https://example.com",
            source_group="Test Corp",
        )
        db_session.add(source)
        db_session.flush()

        extraction = Extraction(
            project_id=project.id,
            source_id=source.id,
            data={"fact_text": "Test fact", "category": "technical"},
            extraction_type="technical_fact",
            source_group="Test Corp",
        )
        db_session.add(extraction)
        db_session.commit()

        assert extraction.id is not None
        assert extraction.project_id == project.id
        assert extraction.source_id == source.id
        assert extraction.data["fact_text"] == "Test fact"
        assert extraction.extraction_type == "technical_fact"
        assert extraction.source_group == "Test Corp"
        assert isinstance(extraction.created_at, datetime)

    def test_create_extraction_with_all_fields(self, db_session):
        """Should create extraction with all fields populated."""
        project = Project(
            name="extraction_full",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            uri="https://example.com",
            source_group="Test Corp",
        )
        db_session.add(source)
        db_session.flush()

        extraction = Extraction(
            project_id=project.id,
            source_id=source.id,
            data={
                "fact_text": "API supports OAuth 2.0",
                "category": "security",
                "confidence": 0.95,
            },
            extraction_type="technical_fact",
            source_group="Test Corp",
            confidence=0.95,
            profile_used="security",
            chunk_index=2,
            chunk_context={"header_path": ["Security", "Authentication"]},
            embedding_id="emb_123",
        )
        db_session.add(extraction)
        db_session.commit()

        assert extraction.data["category"] == "security"
        assert extraction.confidence == 0.95
        assert extraction.profile_used == "security"
        assert extraction.chunk_index == 2
        assert extraction.chunk_context["header_path"] == ["Security", "Authentication"]
        assert extraction.embedding_id == "emb_123"

    def test_extraction_has_relationships(self, db_session):
        """Should have relationships to project and source."""
        project = Project(
            name="rel_test",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            uri="https://example.com",
            source_group="Test",
        )
        db_session.add(source)
        db_session.flush()

        extraction = Extraction(
            project_id=project.id,
            source_id=source.id,
            data={"text": "test"},
            extraction_type="test",
            source_group="Test",
        )
        db_session.add(extraction)
        db_session.commit()

        db_session.refresh(extraction)
        db_session.refresh(project)
        db_session.refresh(source)

        assert extraction.project == project
        assert extraction.source == source
        assert extraction in project.extractions
        assert extraction in source.extractions


class TestEntityModel:
    """Test Entity ORM model."""

    def test_create_entity_with_minimal_fields(self, db_session):
        """Should create entity with required fields."""
        project = Project(
            name="entity_test",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        entity = Entity(
            project_id=project.id,
            source_group="Test Corp",
            entity_type="feature",
            value="SSO Authentication",
            normalized_value="sso_authentication",
        )
        db_session.add(entity)
        db_session.commit()

        assert entity.id is not None
        assert entity.project_id == project.id
        assert entity.source_group == "Test Corp"
        assert entity.entity_type == "feature"
        assert entity.value == "SSO Authentication"
        assert entity.normalized_value == "sso_authentication"
        assert entity.attributes == {}
        assert isinstance(entity.created_at, datetime)

    def test_create_entity_with_attributes(self, db_session):
        """Should create entity with JSONB attributes."""
        project = Project(
            name="entity_attr_test",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        entity = Entity(
            project_id=project.id,
            source_group="Test Corp",
            entity_type="limit",
            value="100 requests/minute",
            normalized_value="rate_limit_100_per_min",
            attributes={"numeric_value": 100, "unit": "requests/minute"},
        )
        db_session.add(entity)
        db_session.commit()

        assert entity.attributes["numeric_value"] == 100
        assert entity.attributes["unit"] == "requests/minute"

    def test_entity_unique_constraint(self, db_session):
        """Should enforce unique constraint on (project_id, source_group, entity_type, normalized_value)."""
        project = Project(
            name="entity_unique",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        entity1 = Entity(
            project_id=project.id,
            source_group="Corp A",
            entity_type="feature",
            value="SSO",
            normalized_value="sso",
        )
        db_session.add(entity1)
        db_session.commit()

        entity2 = Entity(
            project_id=project.id,
            source_group="Corp A",
            entity_type="feature",
            value="SSO",  # Same normalized value
            normalized_value="sso",
        )
        db_session.add(entity2)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()


class TestExtractionEntityModel:
    """Test ExtractionEntity junction table model."""

    def test_create_extraction_entity_link(self, db_session):
        """Should create link between extraction and entity."""
        project = Project(
            name="junction_test",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            uri="https://example.com",
            source_group="Test",
        )
        db_session.add(source)
        db_session.flush()

        extraction = Extraction(
            project_id=project.id,
            source_id=source.id,
            data={"text": "test"},
            extraction_type="fact",
            source_group="Test",
        )
        db_session.add(extraction)
        db_session.flush()

        entity = Entity(
            project_id=project.id,
            source_group="Test",
            entity_type="feature",
            value="Test Feature",
            normalized_value="test_feature",
        )
        db_session.add(entity)
        db_session.flush()

        link = ExtractionEntity(
            extraction_id=extraction.id,
            entity_id=entity.id,
            role="subject",
        )
        db_session.add(link)
        db_session.commit()

        assert link.id is not None
        assert link.extraction_id == extraction.id
        assert link.entity_id == entity.id
        assert link.role == "subject"
        assert isinstance(link.created_at, datetime)

    def test_extraction_entity_default_role(self, db_session):
        """Should use 'mention' as default role."""
        project = Project(
            name="role_test",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            uri="https://example.com",
            source_group="Test",
        )
        db_session.add(source)
        db_session.flush()

        extraction = Extraction(
            project_id=project.id,
            source_id=source.id,
            data={"text": "test"},
            extraction_type="fact",
            source_group="Test",
        )
        entity = Entity(
            project_id=project.id,
            source_group="Test",
            entity_type="feature",
            value="Feature",
            normalized_value="feature",
        )
        db_session.add_all([extraction, entity])
        db_session.flush()

        link = ExtractionEntity(
            extraction_id=extraction.id,
            entity_id=entity.id,
        )
        db_session.add(link)
        db_session.commit()

        assert link.role == "mention"

    def test_extraction_entity_unique_constraint(self, db_session):
        """Should enforce unique constraint on (extraction_id, entity_id, role)."""
        project = Project(
            name="unique_junction",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(project)
        db_session.flush()

        source = Source(
            project_id=project.id,
            uri="https://example.com",
            source_group="Test",
        )
        db_session.add(source)
        db_session.flush()

        extraction = Extraction(
            project_id=project.id,
            source_id=source.id,
            data={"text": "test"},
            extraction_type="fact",
            source_group="Test",
        )
        entity = Entity(
            project_id=project.id,
            source_group="Test",
            entity_type="feature",
            value="Feature",
            normalized_value="feature",
        )
        db_session.add_all([extraction, entity])
        db_session.flush()

        link1 = ExtractionEntity(
            extraction_id=extraction.id,
            entity_id=entity.id,
            role="subject",
        )
        db_session.add(link1)
        db_session.commit()

        link2 = ExtractionEntity(
            extraction_id=extraction.id,
            entity_id=entity.id,
            role="subject",  # Same role
        )
        db_session.add(link2)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
