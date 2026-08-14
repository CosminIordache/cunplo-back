from datetime import datetime, UTC
import pytest
from bson import ObjectId

from src.main import container
from src.domain.user import User


class FakeUserRepository:
  """ponytail: repo en memoria, no hace falta Mongo para probar API y servicio."""

  def __init__(self):
    self.users: dict[ObjectId, User] = {}

  async def create(self, user: User) -> User:
    self.users[user.id] = user
    return user

  async def get(self, user_id):
    return self.users.get(user_id)

  async def get_by_email(self, email):
    return next((u for u in self.users.values() if u.email == email), None)

  async def list(self):
    return list(self.users.values())

  async def update(self, user_id, changes):
    user = self.users.get(user_id)
    if not user:
      return None
    for key, value in changes.items():
      setattr(user, key, value)
    user.updated_at = datetime.now(UTC)
    return user

  async def delete(self, user_id) -> bool:
    return self.users.pop(user_id, None) is not None


PAYLOAD = {
  "username": "cosmin",
  "email": "cosmin@example.com",
  "password": "secret",
  "phone": "+34600000000",
  "timezone": "Europe/Madrid",
  "language": "es",
}


@pytest.fixture
def payload() -> dict:
  return dict(PAYLOAD)


@pytest.fixture
def repository() -> FakeUserRepository:
  return FakeUserRepository()


@pytest.fixture
def integration_repository():
  # borrar un usuario pasa por las integraciones: sin este doble iría a Mongo
  from tests.integrations.conftest import FakeIntegrationRepository

  return FakeIntegrationRepository()


@pytest.fixture
def overrides(repository, integration_repository) -> dict:
  return {
    container.user_repository: repository,
    container.integration_repository: integration_repository,
  }


@pytest.fixture
def created_id(client, payload) -> str:
  """Registra un usuario; el TestClient guarda su cookie de sesión, que /users exige."""
  body = client.post("/api/v1/auth/register", json=payload).json()
  return body["user"]["id"]


@pytest.fixture
def other_user(client, created_id, payload) -> dict:
  """Segundo usuario. Ojo: registrarlo pisa la cookie de `created_id` en el cliente."""
  other = {**payload, "email": "otro@example.com"}
  return client.post("/api/v1/auth/register", json=other).json()


@pytest.fixture
def other_id(client, other_user, payload) -> str:
  # Registrar al segundo usuario dejó su cookie puesta: volvemos a la del primero
  client.post(
    "/api/v1/auth/login",
    json={"email": payload["email"], "password": payload["password"]},
  )
  return other_user["user"]["id"]


@pytest.fixture
def login_as_other(client, payload):
  """Cambia la cookie del cliente al segundo usuario."""
  def login():
    client.post(
      "/api/v1/auth/login",
      json={"email": "otro@example.com", "password": payload["password"]},
    )
  return login
