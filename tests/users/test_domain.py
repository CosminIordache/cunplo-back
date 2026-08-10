from bson import ObjectId

from src.domain.user import User
from tests.users.conftest import PAYLOAD


def test_defaults_are_generated():
  user = User(**PAYLOAD)
  assert isinstance(user.id, ObjectId)
  assert user.created_at.tzinfo is not None
