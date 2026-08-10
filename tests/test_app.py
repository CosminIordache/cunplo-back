"""Rutas de la app que no dependen de ninguna entidad."""
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_root_lists_entrypoints():
  response = client.get("/")
  assert response.status_code == 200
  assert response.json()["docs"] == "/docs"


def test_health_is_ok():
  assert client.get("/health").status_code == 200
