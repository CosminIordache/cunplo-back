from typing import Optional

import pytest
from bson import ObjectId

from src.main import container
from src.domain.integration import Integration, Provider
from tests.users.conftest import FakeUserRepository


class FakeIntegrationRepository:
  def __init__(self):
    self.integrations: dict[ObjectId, Integration] = {}

  async def upsert(self, integration: Integration) -> Integration:
    existing = await self.get_by_user(integration.user_id, integration.provider)
    if existing:
      del self.integrations[existing.id]
      integration.id = existing.id
    self.integrations[integration.id] = integration
    return integration

  async def get_by_user(self, user_id: ObjectId, provider: Provider) -> Optional[Integration]:
    return next(
      (i for i in self.integrations.values()
       if i.user_id == user_id and i.provider == provider),
      None,
    )

  async def get_by_account(self, provider: Provider, account_id: str) -> Optional[Integration]:
    return next(
      (i for i in self.integrations.values()
       if i.provider == provider and i.account_id == account_id),
      None,
    )

  async def get_by_email(self, provider: Provider, email: str) -> Optional[Integration]:
    return next(
      (i for i in self.integrations.values()
       if i.provider == provider and i.email == email),
      None,
    )

  async def list_by_user(self, user_id: ObjectId) -> list[Integration]:
    return [i for i in self.integrations.values() if i.user_id == user_id]

  async def delete(self, integration_id: ObjectId, user_id: ObjectId) -> bool:
    found = self.integrations.get(integration_id)
    if not found or found.user_id != user_id:
      return False
    del self.integrations[integration_id]
    return True


@pytest.fixture
def repository() -> FakeUserRepository:
  return FakeUserRepository()


@pytest.fixture
def integration_repository() -> FakeIntegrationRepository:
  return FakeIntegrationRepository()


@pytest.fixture
def overrides(repository, integration_repository) -> dict:
  return {
    container.user_repository: repository,
    container.integration_repository: integration_repository,
  }
