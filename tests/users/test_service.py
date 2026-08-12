import pytest
from bson import ObjectId

from src.domain.user import User
from src.application.use_cases.user_service import EmailAlreadyUsed, UserService
from tests.users.conftest import PAYLOAD

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(repository) -> UserService:
  return UserService(repository)


async def test_create_persists_user(service, repository):
  user = await service.create(User(**PAYLOAD))
  assert repository.users[user.id] is user


async def test_get_returns_none_when_missing(service):
  assert await service.get(ObjectId()) is None


async def test_list_returns_all(service):
  await service.create(User(**PAYLOAD))
  await service.create(User(**{**PAYLOAD, "username": "otro", "email": "otro@example.com"}))
  assert len(await service.list()) == 2


async def test_create_rejects_duplicate_email(service):
  await service.create(User(**PAYLOAD))
  with pytest.raises(EmailAlreadyUsed):
    await service.create(User(**PAYLOAD))


async def test_update_changes_field(service):
  user = await service.create(User(**PAYLOAD))
  updated = await service.update(user.id, {"username": "cosmin2"})
  assert updated.username == "cosmin2"


async def test_update_missing_returns_none(service):
  assert await service.update(ObjectId(), {"username": "x"}) is None


async def test_delete_returns_true_then_false(service):
  user = await service.create(User(**PAYLOAD))
  assert await service.delete(user.id) is True
  assert await service.delete(user.id) is False
