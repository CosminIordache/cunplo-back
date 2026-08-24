from typing import Optional
from dataclasses import asdict
from datetime import datetime, UTC
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.domain.user import AuthProvider, User


def _to_document(user: User) -> dict:
  document = asdict(user)
  document["_id"] = document.pop("id")
  return document


def _to_user(document: dict) -> User:
  document = dict(document)
  document["id"] = document.pop("_id")
  return User(**document)


class MongoUserRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["users"]

  async def create(self, user: User) -> User:
    await self.collection.insert_one(_to_document(user))
    return user

  async def get(self, user_id: ObjectId) -> Optional[User]:
    doc = await self.collection.find_one({"_id": user_id})
    return _to_user(doc) if doc else None

  async def get_by_email(self, email: str) -> Optional[User]:
    doc = await self.collection.find_one({"email": email})
    return _to_user(doc) if doc else None

  async def get_by_auth_account(
    self, auth_provider: AuthProvider, auth_account_id: str
  ) -> Optional[User]:
    doc = await self.collection.find_one(
      {"auth_provider": auth_provider, "auth_account_id": auth_account_id}
    )
    return _to_user(doc) if doc else None

  async def list(self) -> list[User]:
    # Sin paginación, añade skip/limit cuando la colección crezca
    return [_to_user(d) async for d in self.collection.find()]

  async def update(self, user_id: ObjectId, changes: dict) -> Optional[User]:
    changes["updated_at"] = datetime.now(UTC)
    doc = await self.collection.find_one_and_update(
      {"_id": user_id}, {"$set": changes}, return_document=True
    )
    return _to_user(doc) if doc else None

  async def delete(self, user_id: ObjectId) -> bool:
    result = await self.collection.delete_one({"_id": user_id})
    return result.deleted_count == 1
