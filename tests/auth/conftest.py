import pytest

from src.main import container
from tests.users.conftest import FakeUserRepository, PAYLOAD


@pytest.fixture
def payload() -> dict:
  return dict(PAYLOAD)


@pytest.fixture
def repository() -> FakeUserRepository:
  return FakeUserRepository()


@pytest.fixture
def overrides(repository) -> dict:
  return {container.user_repository: repository}
