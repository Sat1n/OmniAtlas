"""Shared pytest fixtures for the OmniAtlas test suite."""

import pytest

from core.parser import LanguageRegistry


@pytest.fixture()
def registry() -> LanguageRegistry:
    """Fresh multi-language parser registry per test."""
    return LanguageRegistry()
