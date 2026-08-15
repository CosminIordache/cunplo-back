from datetime import datetime, UTC
import pytest
from bson import ObjectId

from src.main import container
from src.domain.contact import Contact


class FakeContactRepository:
  """ponytail: repo en memoria; el filtro por user_id se replica tal cual lo hace Mongo."""

  def __init__(self):
    self.contacts: dict[ObjectId, Contact] = {}

  async def create(self, contact: Contact) -> Contact:
    self.contacts[contact.id] = contact
    return contact

  async def get(self, contact_id, user_id):
    contact = self.contacts.get(contact_id)
    return contact if contact and contact.user_id == user_id else None

  async def get_by_email(self, user_id, email):
    return next(
      (c for c in self.contacts.values()
       if c.user_id == user_id and c.email == email),
      None,
    )

  async def get_by_user(self, user_id) -> list[Contact]:
    return [c for c in self.contacts.values() if c.user_id == user_id]

  async def update(self, contact_id, user_id, changes):
    contact = self.contacts.get(contact_id)
    if not contact or contact.user_id != user_id:  # el dueño, o nada
      return None
    for key, value in changes.items():
      setattr(contact, key, value)
    contact.updated_at = datetime.now(UTC)
    return contact

  async def delete(self, contact_id, user_id) -> bool:
    contact = self.contacts.get(contact_id)
    if not contact or contact.user_id != user_id:
      return False
    del self.contacts[contact_id]
    return True


PAYLOAD = {
  "username": "cosmin",
  "email": "cosmin@example.com",
  "password": "secret",
  "timezone": "Europe/Madrid",
  "language": "es",
}


@pytest.fixture
def payload() -> dict:
  return dict(PAYLOAD)


@pytest.fixture
def repository() -> FakeContactRepository:
  return FakeContactRepository()


@pytest.fixture
def overrides(repository) -> dict:
  from tests.users.conftest import FakeUserRepository

  return {
    container.contact_repository: repository,
    container.user_repository: FakeUserRepository(),
  }


@pytest.fixture
def contact_id(client, payload) -> str:
  """Registra al usuario (deja su cookie) y le crea un contacto."""
  client.post("/api/v1/auth/register", json=payload)
  body = client.post(
    "/api/v1/contacts", json={"email": "cliente@example.com", "name": "Cliente"}
  ).json()
  return body["id"]


@pytest.fixture
def login_as_other(client, payload):
  """Registra y deja logueado a un segundo usuario."""
  def login():
    client.post("/api/v1/auth/register", json={**payload, "email": "otro@example.com"})
  return login
