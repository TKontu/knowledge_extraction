"""Tests for database schema validation.

This test suite validates that the generalized schema (projects, sources, extractions)
is properly created with all expected tables, columns, indexes, and constraints.
"""

import pytest
from sqlalchemy import inspect, text
from database import engine


class TestGeneralizedSchema:
    """Test that generalized schema tables exist with proper structure."""

    def test_projects_table_exists(self):
        """Projects table should exist with all required columns."""
        inspector = inspect(engine)
        assert "projects" in inspector.get_table_names()

        columns = {col["name"]: col for col in inspector.get_columns("projects")}

        # Check required columns exist
        assert "id" in columns
        assert "name" in columns
        assert "description" in columns
        assert "source_config" in columns
        assert "extraction_schema" in columns
        assert "entity_types" in columns
        assert "prompt_templates" in columns
        assert "is_template" in columns
        assert "is_active" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

    def test_projects_table_constraints(self):
        """Projects table should have proper constraints."""
        inspector = inspect(engine)

        # Check primary key
        pk = inspector.get_pk_constraint("projects")
        assert "id" in pk["constrained_columns"]

        # Check unique constraint on name
        unique_constraints = inspector.get_unique_constraints("projects")
        name_is_unique = any(
            "name" in uc["column_names"] for uc in unique_constraints
        )
        assert name_is_unique

    def test_projects_table_indexes(self):
        """Projects table should have proper indexes."""
        inspector = inspect(engine)
        indexes = {idx["name"]: idx for idx in inspector.get_indexes("projects")}

        # Check expected indexes exist
        assert "idx_projects_name" in indexes
        assert "idx_projects_active" in indexes

    def test_sources_table_exists(self):
        """Sources table should exist with all required columns."""
        inspector = inspect(engine)
        assert "sources" in inspector.get_table_names()

        columns = {col["name"]: col for col in inspector.get_columns("sources")}

        # Check required columns
        assert "id" in columns
        assert "project_id" in columns
        assert "source_type" in columns
        assert "uri" in columns
        assert "source_group" in columns
        assert "title" in columns
        assert "content" in columns
        assert "raw_content" in columns
        assert "metadata" in columns
        assert "outbound_links" in columns
        assert "status" in columns
        assert "fetched_at" in columns
        assert "created_at" in columns

    def test_sources_table_constraints(self):
        """Sources table should have proper constraints."""
        inspector = inspect(engine)

        # Check primary key
        pk = inspector.get_pk_constraint("sources")
        assert "id" in pk["constrained_columns"]

        # Check foreign key to projects
        fks = inspector.get_foreign_keys("sources")
        project_fk = next(
            (fk for fk in fks if "project_id" in fk["constrained_columns"]), None
        )
        assert project_fk is not None
        assert project_fk["referred_table"] == "projects"

        # Check unique constraint on (project_id, uri)
        unique_constraints = inspector.get_unique_constraints("sources")
        project_uri_unique = any(
            set(uc["column_names"]) == {"project_id", "uri"}
            for uc in unique_constraints
        )
        assert project_uri_unique

    def test_sources_table_indexes(self):
        """Sources table should have proper indexes."""
        inspector = inspect(engine)
        indexes = {idx["name"]: idx for idx in inspector.get_indexes("sources")}

        # Check expected indexes
        assert "idx_sources_project" in indexes
        assert "idx_sources_group" in indexes
        assert "idx_sources_status" in indexes

    def test_extractions_table_exists(self):
        """Extractions table should exist with all required columns."""
        inspector = inspect(engine)
        assert "extractions" in inspector.get_table_names()

        columns = {col["name"]: col for col in inspector.get_columns("extractions")}

        # Check required columns
        assert "id" in columns
        assert "project_id" in columns
        assert "source_id" in columns
        assert "data" in columns  # JSONB field
        assert "extraction_type" in columns
        assert "source_group" in columns
        assert "confidence" in columns
        assert "profile_used" in columns
        assert "chunk_index" in columns
        assert "chunk_context" in columns
        assert "embedding_id" in columns
        assert "extracted_at" in columns
        assert "created_at" in columns

    def test_extractions_table_constraints(self):
        """Extractions table should have proper constraints."""
        inspector = inspect(engine)

        # Check primary key
        pk = inspector.get_pk_constraint("extractions")
        assert "id" in pk["constrained_columns"]

        # Check foreign keys
        fks = inspector.get_foreign_keys("extractions")

        project_fk = next(
            (fk for fk in fks if "project_id" in fk["constrained_columns"]), None
        )
        assert project_fk is not None
        assert project_fk["referred_table"] == "projects"

        source_fk = next(
            (fk for fk in fks if "source_id" in fk["constrained_columns"]), None
        )
        assert source_fk is not None
        assert source_fk["referred_table"] == "sources"

    def test_extractions_table_indexes(self):
        """Extractions table should have proper indexes including GIN index on JSONB."""
        inspector = inspect(engine)
        indexes = {idx["name"]: idx for idx in inspector.get_indexes("extractions")}

        # Check expected indexes
        assert "idx_extractions_project" in indexes
        assert "idx_extractions_source" in indexes
        assert "idx_extractions_group" in indexes
        assert "idx_extractions_type" in indexes
        assert "idx_extractions_confidence" in indexes
        assert "idx_extractions_data" in indexes  # GIN index on JSONB

    def test_entities_table_exists(self):
        """Entities table should exist with all required columns."""
        inspector = inspect(engine)
        assert "entities" in inspector.get_table_names()

        columns = {col["name"]: col for col in inspector.get_columns("entities")}

        # Check required columns
        assert "id" in columns
        assert "project_id" in columns
        assert "source_group" in columns
        assert "entity_type" in columns
        assert "value" in columns
        assert "normalized_value" in columns
        assert "attributes" in columns
        assert "created_at" in columns

    def test_entities_table_constraints(self):
        """Entities table should have proper constraints."""
        inspector = inspect(engine)

        # Check primary key
        pk = inspector.get_pk_constraint("entities")
        assert "id" in pk["constrained_columns"]

        # Check foreign key to projects
        fks = inspector.get_foreign_keys("entities")
        project_fk = next(
            (fk for fk in fks if "project_id" in fk["constrained_columns"]), None
        )
        assert project_fk is not None
        assert project_fk["referred_table"] == "projects"

        # Check unique constraint on (project_id, source_group, entity_type, normalized_value)
        unique_constraints = inspector.get_unique_constraints("entities")
        entity_unique = any(
            set(uc["column_names"])
            == {"project_id", "source_group", "entity_type", "normalized_value"}
            for uc in unique_constraints
        )
        assert entity_unique

    def test_entities_table_indexes(self):
        """Entities table should have proper indexes."""
        inspector = inspect(engine)
        indexes = {idx["name"]: idx for idx in inspector.get_indexes("entities")}

        # Check expected indexes
        assert "idx_entities_project" in indexes
        assert "idx_entities_group" in indexes
        assert "idx_entities_type" in indexes

    def test_extraction_entities_junction_table_exists(self):
        """Extraction_entities junction table should exist."""
        inspector = inspect(engine)
        assert "extraction_entities" in inspector.get_table_names()

        columns = {
            col["name"]: col for col in inspector.get_columns("extraction_entities")
        }

        # Check required columns
        assert "id" in columns
        assert "extraction_id" in columns
        assert "entity_id" in columns
        assert "role" in columns
        assert "created_at" in columns

    def test_extraction_entities_constraints(self):
        """Extraction_entities should have proper foreign keys."""
        inspector = inspect(engine)

        # Check foreign keys
        fks = inspector.get_foreign_keys("extraction_entities")

        extraction_fk = next(
            (fk for fk in fks if "extraction_id" in fk["constrained_columns"]), None
        )
        assert extraction_fk is not None
        assert extraction_fk["referred_table"] == "extractions"

        entity_fk = next(
            (fk for fk in fks if "entity_id" in fk["constrained_columns"]), None
        )
        assert entity_fk is not None
        assert entity_fk["referred_table"] == "entities"

        # Check unique constraint
        unique_constraints = inspector.get_unique_constraints("extraction_entities")
        junction_unique = any(
            set(uc["column_names"]) == {"extraction_id", "entity_id", "role"}
            for uc in unique_constraints
        )
        assert junction_unique

    def test_jobs_table_has_project_id(self):
        """Jobs table should have project_id column and foreign key."""
        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("jobs")}

        assert "project_id" in columns

        # Check foreign key
        fks = inspector.get_foreign_keys("jobs")
        project_fk = next(
            (fk for fk in fks if "project_id" in fk["constrained_columns"]), None
        )
        assert project_fk is not None
        assert project_fk["referred_table"] == "projects"

    def test_jobs_table_has_project_index(self):
        """Jobs table should have index on project_id."""
        inspector = inspect(engine)
        indexes = {idx["name"]: idx for idx in inspector.get_indexes("jobs")}

        assert "idx_jobs_project" in indexes

    def test_reports_table_updated_for_generalization(self):
        """Reports table should have project_id and generalized field names."""
        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("reports")}

        # Check new columns
        assert "project_id" in columns
        assert "source_groups" in columns  # replaces companies
        assert "extraction_ids" in columns  # replaces fact_ids

        # Check foreign key
        fks = inspector.get_foreign_keys("reports")
        project_fk = next(
            (fk for fk in fks if "project_id" in fk["constrained_columns"]), None
        )
        assert project_fk is not None
        assert project_fk["referred_table"] == "projects"


class TestLegacyTablesRemain:
    """Verify that legacy tables remain unchanged during transition."""

    def test_pages_table_still_exists(self):
        """Legacy pages table should still exist."""
        inspector = inspect(engine)
        assert "pages" in inspector.get_table_names()

    def test_facts_table_still_exists(self):
        """Legacy facts table should still exist."""
        inspector = inspect(engine)
        assert "facts" in inspector.get_table_names()

    def test_profiles_table_still_exists(self):
        """Legacy profiles table should still exist."""
        inspector = inspect(engine)
        assert "profiles" in inspector.get_table_names()
