"""Fixtures compartidas por todas las entidades."""
import pytest
from fastapi.testclient import TestClient

from src.main import app, container


@pytest.fixture
def client(overrides):
  """`overrides` lo define el conftest de cada entidad: {provider: doble de test}."""
  for provider, replacement in overrides.items():
    provider.override(replacement)
  with TestClient(app) as test_client:
    yield test_client
  for provider in overrides:
    provider.reset_override()
