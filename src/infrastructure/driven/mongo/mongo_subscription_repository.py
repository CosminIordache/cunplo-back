from dataclasses import asdict
from datetime import datetime, UTC
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from src.domain.subscription import Subscription


def _to_document(subscription: Subscription) -> dict:
  document = asdict(subscription)
  document["_id"] = document.pop("id")
  return document


def _to_subscription(document: dict) -> Subscription:
  document = dict(document)
  document["id"] = document.pop("_id")
  return Subscription(**document)


class MongoSubscriptionRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["subscriptions"]

  async def create(self, subscription: Subscription) -> Subscription:
    await self.collection.insert_one(_to_document(subscription))
    return subscription

  async def get_by_user(self, user_id: ObjectId) -> Optional[Subscription]:
    document = await self.collection.find_one({"user_id": user_id})
    return _to_subscription(document) if document else None

  async def update(self, user_id: ObjectId, changes: dict) -> Optional[Subscription]:
    changes = {**changes, "updated_at": datetime.now(UTC)}
    document = await self.collection.find_one_and_update(
      {"user_id": user_id},
      {"$set": changes},
      return_document=ReturnDocument.AFTER,
    )
    return _to_subscription(document) if document else None

  async def delete_by_user(self, user_id: ObjectId) -> bool:
    result = await self.collection.delete_one({"user_id": user_id})
    return result.deleted_count > 0
