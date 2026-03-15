"""Tests for Source unique constraint and upsert functionality.

TDD: These tests define the expected behavior for source deduplication.
"""

from orm_models import Source


class TestSourceUniqueConstraint:
    """Tests for Source unique constraint on (project_id, uri)."""

    def test_source_has_unique_constraint(self):
        """Should have unique constraint on (project_id, uri)."""
        # Check that Source has __table_args__ with UniqueConstraint
        assert hasattr(Source, "__table_args__"), "Source missing __table_args__"

        # Find the unique constraint
        table_args = Source.__table_args__
        if isinstance(table_args, tuple):
            constraints = [arg for arg in table_args if hasattr(arg, "columns")]
        else:
            constraints = []

        # Find unique constraint on project_id and uri
        found_constraint = False
        for constraint in constraints:
            if hasattr(constraint, "columns"):
                col_names = [c.name for c in constraint.columns]
                if "project_id" in col_names and "uri" in col_names:
                    found_constraint = True
                    break

        assert found_constraint, "Source missing UniqueConstraint on (project_id, uri)"

    def test_source_unique_constraint_name(self):
        """Should have named constraint for easier migration management."""
        table_args = Source.__table_args__
        if isinstance(table_args, tuple):
            constraints = [arg for arg in table_args if hasattr(arg, "name")]
        else:
            constraints = []

        constraint_names = [c.name for c in constraints if c.name]
        assert "uq_sources_project_uri" in constraint_names, (
            f"Expected 'uq_sources_project_uri' constraint, found: {constraint_names}"
        )
