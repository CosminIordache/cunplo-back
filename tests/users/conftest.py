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
def overrides(repository) -> dict:
  return {container.user_repository: repository}


@pytest.fixture
def created_id(client, payload) -> str:
  """Registra un usuario y deja su Bearer puesto en el cliente: /users lo exige."""
  body = client.post("/api/v1/auth/register", json=payload).json()
  client.headers["Authorization"] = f"Bearer {body['access_token']}"
  return body["user"]["id"]


@pytest.fixture
def other_user(client, created_id, payload) -> dict:
  """Segundo usuario; el cliente conserva el Bearer de `created_id`."""
  other = {**payload, "email": "otro@example.com"}
  return client.post("/api/v1/auth/register", json=other).json()


@pytest.fixture
def other_id(other_user) -> str:
  return other_user["user"]["id"]


@pytest.fixture
def other_token(other_user) -> str:
  return other_user["access_token"]
