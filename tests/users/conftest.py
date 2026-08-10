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
  return client.post("/api/v1/users/create", json=payload).json()["id"]
