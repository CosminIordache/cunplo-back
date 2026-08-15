from src.domain.user import User
from src.infrastructure.driven.mongo.mongo_user_repository import _to_document, _to_user
from tests.users.conftest import PAYLOAD


def test_to_document_uses_mongo_id_key():
  document = _to_document(User(**PAYLOAD))
  assert "_id" in document and "id" not in document


def test_to_user_roundtrip():
  user = User(**PAYLOAD)
  assert _to_user(_to_document(user)) == user
