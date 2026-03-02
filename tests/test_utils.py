"""Tests for shared utility functions."""

import math

import pytest

from utils import cosine_similarity


class TestCosineSimilarity:
    """Tests for cosine_similarity function."""

    def test_identical_vectors(self):
        """Identical vectors should have similarity 1.0."""
        vec = [1.0, 2.0, 3.0]
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        """Orthogonal vectors should have similarity 0.0."""
        vec_a = [1.0, 0.0]
        vec_b = [0.0, 1.0]
        assert cosine_similarity(vec_a, vec_b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        """Opposite vectors should have similarity -1.0."""
        vec_a = [1.0, 0.0]
        vec_b = [-1.0, 0.0]
        assert cosine_similarity(vec_a, vec_b) == pytest.approx(-1.0)

    def test_mismatched_lengths(self):
        """Mismatched vector lengths should return 0.0."""
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [1.0, 2.0]
        assert cosine_similarity(vec_a, vec_b) == 0.0

    def test_zero_vector(self):
        """Zero vector should return 0.0."""
        vec_a = [0.0, 0.0, 0.0]
        vec_b = [1.0, 2.0, 3.0]
        assert cosine_similarity(vec_a, vec_b) == 0.0
        assert cosine_similarity(vec_b, vec_a) == 0.0
