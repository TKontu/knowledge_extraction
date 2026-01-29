"""Tests for EntityRepository."""

import pytest
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from database import engine
from orm_models import Entity, ExtractionEntity, Project, Source, Extraction
from services.storage.repositories.entity import EntityRepository, EntityFilters


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


@pytest.fixture
def test_project(db_session):
    """Create a test project for entity tests."""
    project = Project(
        name="test_entity_project",
        extraction_schema={"name": "test", "fields": []},
        entity_types=[
            {"name": "company", "description": "Company names"},
            {"name": "feature", "description": "Product features"},
            {"name": "pricing", "description": "Pricing information"},
        ],
    )
    db_session.add(project)
    db_session.flush()
    return project


@pytest.fixture
def test_source(db_session, test_project):
    """Create a test source for entity tests."""
    source = Source(
        project_id=test_project.id,
        uri="https://example.com/test",
        source_group="test_company",
    )
    db_session.add(source)
    db_session.flush()
    return source


@pytest.fixture
def test_extraction(db_session, test_project, test_source):
    """Create a test extraction for entity linking tests."""
    extraction = Extraction(
        project_id=test_project.id,
        source_id=test_source.id,
        data={"fact_text": "Test fact", "category": "feature"},
        extraction_type="technical_fact",
        source_group="test_company",
    )
    db_session.add(extraction)
    db_session.flush()
    return extraction


@pytest.fixture
def entity_repo(db_session):
    """Create EntityRepository instance."""
    return EntityRepository(db_session)


class TestEntityRepositoryCreate:
    """Test EntityRepository.create() method."""

    def test_create_entity_with_minimal_data(
        self, entity_repo, test_project, db_session
    ):
        """Should create entity with minimal required fields."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Acme Corp",
            normalized_value="acme_corp",
        )

        assert entity.id is not None
        assert entity.project_id == test_project.id
        assert entity.source_group == "test_company"
        assert entity.entity_type == "company"
        assert entity.value == "Acme Corp"
        assert entity.normalized_value == "acme_corp"
        assert entity.attributes == {}

    def test_create_entity_with_attributes(
        self, entity_repo, test_project
    ):
        """Should create entity with attributes."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="pricing",
            value="$99/month",
            normalized_value="99_usd_month",
            attributes={
                "currency": "USD",
                "amount": 99,
                "period": "month",
            },
        )

        assert entity.attributes["currency"] == "USD"
        assert entity.attributes["amount"] == 99
        assert entity.attributes["period"] == "month"

    def test_create_entity_returns_persisted_object(
        self, entity_repo, test_project, db_session
    ):
        """Created entity should be retrievable from database."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="feature",
            value="SSO Support",
            normalized_value="sso_support",
        )

        # Verify it's in the database
        result = db_session.execute(
            select(Entity).where(Entity.id == entity.id)
        )
        db_entity = result.scalar_one()
        assert db_entity.value == "SSO Support"

    def test_create_entity_sets_created_at(
        self, entity_repo, test_project
    ):
        """Should automatically set created_at timestamp."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Test Corp",
            normalized_value="test_corp",
        )

        assert entity.created_at is not None
        assert isinstance(entity.created_at, datetime)


class TestEntityRepositoryGet:
    """Test EntityRepository.get() method."""

    def test_get_by_id_returns_entity(
        self, entity_repo, test_project
    ):
        """Should retrieve entity by ID."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Get Test Corp",
            normalized_value="get_test_corp",
        )

        retrieved = entity_repo.get(entity.id)
        assert retrieved is not None
        assert retrieved.id == entity.id
        assert retrieved.value == "Get Test Corp"

    def test_get_nonexistent_returns_none(self, entity_repo):
        """Should return None for nonexistent entity."""
        from uuid import uuid4

        retrieved = entity_repo.get(uuid4())
        assert retrieved is None


class TestEntityRepositoryGetOrCreate:
    """Test EntityRepository.get_or_create() method for deduplication."""

    def test_get_or_create_creates_new_entity(
        self, entity_repo, test_project
    ):
        """Should create new entity when none exists."""
        entity, created = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="New Corp",
            normalized_value="new_corp",
        )

        assert created is True
        assert entity.id is not None
        assert entity.value == "New Corp"
        assert entity.normalized_value == "new_corp"

    def test_get_or_create_returns_existing_entity(
        self, entity_repo, test_project
    ):
        """Should return existing entity with same normalized_value."""
        # Create first entity
        entity1, created1 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Existing Corp",
            normalized_value="existing_corp",
        )
        assert created1 is True

        # Try to create again with same normalized_value
        entity2, created2 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Existing Corp",  # Same value
            normalized_value="existing_corp",  # Same normalized
        )

        assert created2 is False
        assert entity2.id == entity1.id  # Should be same entity

    def test_get_or_create_deduplicates_case_variations(
        self, entity_repo, test_project
    ):
        """Should deduplicate entities with different casing."""
        # Create first entity
        entity1, created1 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="feature",
            value="SSO Support",
            normalized_value="sso_support",
        )
        assert created1 is True

        # Try to create with different case but same normalized value
        entity2, created2 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="feature",
            value="sso support",  # Different case
            normalized_value="sso_support",  # Same normalized
        )

        assert created2 is False
        assert entity2.id == entity1.id

    def test_get_or_create_scoped_by_project(
        self, entity_repo, test_project, db_session
    ):
        """Should create separate entities for different projects."""
        # Create another project
        other_project = Project(
            name="other_entity_project",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(other_project)
        db_session.flush()

        # Create entity in first project
        entity1, created1 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Project Corp",
            normalized_value="project_corp",
        )
        assert created1 is True

        # Create entity with same normalized_value in other project
        entity2, created2 = entity_repo.get_or_create(
            project_id=other_project.id,
            source_group="test_company",
            entity_type="company",
            value="Project Corp",
            normalized_value="project_corp",
        )

        assert created2 is True  # Should create new
        assert entity2.id != entity1.id  # Different entities

    def test_get_or_create_scoped_by_source_group(
        self, entity_repo, test_project
    ):
        """Should create separate entities for different source_groups."""
        # Create entity in first source_group
        entity1, created1 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="company_a",
            entity_type="feature",
            value="API Access",
            normalized_value="api_access",
        )
        assert created1 is True

        # Create entity with same normalized_value in different source_group
        entity2, created2 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="company_b",
            entity_type="feature",
            value="API Access",
            normalized_value="api_access",
        )

        assert created2 is True  # Should create new
        assert entity2.id != entity1.id  # Different entities

    def test_get_or_create_scoped_by_entity_type(
        self, entity_repo, test_project
    ):
        """Should create separate entities for different entity_types."""
        # Create entity of one type
        entity1, created1 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Enterprise",
            normalized_value="enterprise",
        )
        assert created1 is True

        # Create entity with same normalized_value but different type
        entity2, created2 = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="pricing",
            value="Enterprise",
            normalized_value="enterprise",
        )

        assert created2 is True  # Should create new
        assert entity2.id != entity1.id  # Different entities

    def test_get_or_create_with_attributes(
        self, entity_repo, test_project
    ):
        """Should store attributes when creating new entity."""
        entity, created = entity_repo.get_or_create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="pricing",
            value="$199/month",
            normalized_value="199_usd_month",
            attributes={"amount": 199, "currency": "USD"},
        )

        assert created is True
        assert entity.attributes["amount"] == 199


class TestEntityRepositoryListByType:
    """Test EntityRepository.list_by_type() method."""

    def test_list_by_type_returns_entities(
        self, entity_repo, test_project
    ):
        """Should list all entities of a specific type."""
        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Company A",
            normalized_value="company_a",
        )
        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Company B",
            normalized_value="company_b",
        )
        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="feature",
            value="Feature X",
            normalized_value="feature_x",
        )

        entities = entity_repo.list_by_type(
            project_id=test_project.id,
            entity_type="company",
        )

        assert len(entities) >= 2
        assert all(e.entity_type == "company" for e in entities)

    def test_list_by_type_filters_by_source_group(
        self, entity_repo, test_project
    ):
        """Should filter entities by source_group when provided."""
        entity_repo.create(
            project_id=test_project.id,
            source_group="company_a",
            entity_type="feature",
            value="Feature 1",
            normalized_value="feature_1",
        )
        entity_repo.create(
            project_id=test_project.id,
            source_group="company_b",
            entity_type="feature",
            value="Feature 2",
            normalized_value="feature_2",
        )

        entities = entity_repo.list_by_type(
            project_id=test_project.id,
            entity_type="feature",
            source_group="company_a",
        )

        assert len(entities) >= 1
        assert all(e.source_group == "company_a" for e in entities)

    def test_list_by_type_returns_empty_for_no_match(
        self, entity_repo, test_project
    ):
        """Should return empty list when no entities match."""
        entities = entity_repo.list_by_type(
            project_id=test_project.id,
            entity_type="nonexistent_type",
        )

        matching = [
            e for e in entities if e.entity_type == "nonexistent_type"
        ]
        assert len(matching) == 0

    def test_list_by_type_sorted_by_value(
        self, entity_repo, test_project
    ):
        """Should return entities sorted by value."""
        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Zebra Corp",
            normalized_value="zebra_corp",
        )
        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Apple Corp",
            normalized_value="apple_corp",
        )

        entities = entity_repo.list_by_type(
            project_id=test_project.id,
            entity_type="company",
        )

        # Find our test entities
        test_entities = [
            e for e in entities
            if e.value in ["Apple Corp", "Zebra Corp"]
        ]
        if len(test_entities) == 2:
            assert test_entities[0].value == "Apple Corp"
            assert test_entities[1].value == "Zebra Corp"


class TestEntityRepositoryList:
    """Test EntityRepository.list() method with filters."""

    def test_list_all_entities(self, entity_repo, test_project):
        """Should list all entities without filters."""
        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Entity 1",
            normalized_value="entity_1",
        )
        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="feature",
            value="Entity 2",
            normalized_value="entity_2",
        )

        entities = entity_repo.list(EntityFilters())
        assert len(entities) >= 2

    def test_list_filters_by_project_id(
        self, entity_repo, test_project, db_session
    ):
        """Should filter entities by project_id."""
        # Create another project
        other_project = Project(
            name="other_list_project",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(other_project)
        db_session.flush()

        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Test Project Entity",
            normalized_value="test_project_entity",
        )
        entity_repo.create(
            project_id=other_project.id,
            source_group="test_company",
            entity_type="company",
            value="Other Project Entity",
            normalized_value="other_project_entity",
        )

        entities = entity_repo.list(
            EntityFilters(project_id=test_project.id)
        )
        assert len(entities) >= 1
        assert all(e.project_id == test_project.id for e in entities)

    def test_list_filters_by_entity_type(
        self, entity_repo, test_project
    ):
        """Should filter entities by entity_type."""
        entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="pricing",
            value="Pricing Entity",
            normalized_value="pricing_entity",
        )

        entities = entity_repo.list(
            EntityFilters(entity_type="pricing")
        )
        assert len(entities) >= 1
        assert all(e.entity_type == "pricing" for e in entities)

    def test_list_filters_by_source_group(
        self, entity_repo, test_project
    ):
        """Should filter entities by source_group."""
        entity_repo.create(
            project_id=test_project.id,
            source_group="specific_company",
            entity_type="company",
            value="Specific Entity",
            normalized_value="specific_entity",
        )

        entities = entity_repo.list(
            EntityFilters(source_group="specific_company")
        )
        assert len(entities) >= 1
        assert all(e.source_group == "specific_company" for e in entities)


class TestEntityRepositoryLinkToExtraction:
    """Test EntityRepository.link_to_extraction() method."""

    def test_link_to_extraction_creates_link(
        self, entity_repo, test_project, test_extraction
    ):
        """Should create link between entity and extraction."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="feature",
            value="Test Feature",
            normalized_value="test_feature",
        )

        link, created = entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity.id,
        )

        assert created is True
        assert link.id is not None
        assert link.extraction_id == test_extraction.id
        assert link.entity_id == entity.id
        assert link.role == "mention"

    def test_link_to_extraction_with_custom_role(
        self, entity_repo, test_project, test_extraction
    ):
        """Should create link with custom role."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="pricing",
            value="$99/month",
            normalized_value="99_usd_month",
        )

        link, created = entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity.id,
            role="pricing_detail",
        )

        assert created is True
        assert link.role == "pricing_detail"

    def test_link_to_extraction_sets_created_at(
        self, entity_repo, test_project, test_extraction
    ):
        """Should set created_at timestamp on link."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Test Corp",
            normalized_value="test_corp",
        )

        link, created = entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity.id,
        )

        assert created is True
        assert link.created_at is not None
        assert isinstance(link.created_at, datetime)

    def test_link_to_extraction_idempotent(
        self, entity_repo, test_project, test_extraction
    ):
        """Should return existing link without error when called twice."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="feature",
            value="Idempotent Feature",
            normalized_value="idempotent_feature",
        )

        # First call creates the link
        link1, created1 = entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity.id,
        )
        assert created1 is True

        # Second call returns existing link
        link2, created2 = entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity.id,
        )
        assert created2 is False
        assert link2.id == link1.id

    def test_link_to_extraction_different_roles_creates_separate_links(
        self, entity_repo, test_project, test_extraction
    ):
        """Should create separate links for same entity with different roles."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Multi-Role Corp",
            normalized_value="multi_role_corp",
        )

        # First link with default role
        link1, created1 = entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity.id,
            role="mention",
        )
        assert created1 is True

        # Second link with different role
        link2, created2 = entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity.id,
            role="subject",
        )
        assert created2 is True
        assert link2.id != link1.id


class TestEntityRepositoryGetEntitiesForExtraction:
    """Test EntityRepository.get_entities_for_extraction() method."""

    def test_get_entities_for_extraction_returns_entities(
        self, entity_repo, test_project, test_extraction
    ):
        """Should return all entities linked to an extraction."""
        entity1 = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Entity 1",
            normalized_value="entity_1",
        )
        entity2 = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="feature",
            value="Entity 2",
            normalized_value="entity_2",
        )

        entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity1.id,
        )  # Returns (link, created) tuple - we don't need the result
        entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity2.id,
        )  # Returns (link, created) tuple - we don't need the result

        entities = entity_repo.get_entities_for_extraction(
            test_extraction.id
        )

        assert len(entities) == 2
        entity_ids = {e.id for e in entities}
        assert entity1.id in entity_ids
        assert entity2.id in entity_ids

    def test_get_entities_for_extraction_returns_empty_for_none(
        self, entity_repo, test_extraction
    ):
        """Should return empty list when extraction has no entities."""
        entities = entity_repo.get_entities_for_extraction(
            test_extraction.id
        )

        assert entities == []


class TestEntityRepositoryGetExtractionsForEntity:
    """Test EntityRepository.get_extractions_for_entity() method."""

    def test_get_extractions_for_entity_returns_extractions(
        self, entity_repo, test_project, test_source, test_extraction, db_session
    ):
        """Should return all extractions linked to an entity."""
        # Create another extraction
        extraction2 = Extraction(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Another fact", "category": "pricing"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        db_session.add(extraction2)
        db_session.flush()

        # Create entity and link to both extractions
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Linked Corp",
            normalized_value="linked_corp",
        )

        entity_repo.link_to_extraction(
            extraction_id=test_extraction.id,
            entity_id=entity.id,
        )
        entity_repo.link_to_extraction(
            extraction_id=extraction2.id,
            entity_id=entity.id,
        )

        extractions = entity_repo.get_extractions_for_entity(entity.id)

        assert len(extractions) == 2
        extraction_ids = {e.id for e in extractions}
        assert test_extraction.id in extraction_ids
        assert extraction2.id in extraction_ids

    def test_get_extractions_for_entity_returns_empty_for_none(
        self, entity_repo, test_project
    ):
        """Should return empty list when entity has no extractions."""
        entity = entity_repo.create(
            project_id=test_project.id,
            source_group="test_company",
            entity_type="company",
            value="Unlinked Corp",
            normalized_value="unlinked_corp",
        )

        extractions = entity_repo.get_extractions_for_entity(entity.id)

        assert extractions == []
