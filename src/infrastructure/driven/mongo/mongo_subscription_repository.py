from dataclasses import asdict
from datetime import datetime, timedelta, UTC
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from src.domain.subscription import Plan, Subscription, SubscriptionStatus


def _to_document(subscription: Subscription) -> dict:
  document = asdict(subscription)
  document["_id"] = document.pop("id")
  return document


def _to_subscription(document: dict) -> Subscription:
  document = dict(document)
  document["id"] = document.pop("_id")
  # Mongo guarda str: el dominio compara con `is` contra los enums, así que se convierten aquí
  document["plan"] = Plan(document["plan"])
  document["status"] = SubscriptionStatus(document["status"])
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

  async def stats(self) -> dict:
    """Cuántos hay en cada estado, con el mismo derivado que
    Subscription.current_status: CANCELED manda, luego caduca expires_at, luego el guardado."""
    now = datetime.now(UTC)
    derived = {
      "$switch": {
        "branches": [
          {"case": {"$eq": ["$status", SubscriptionStatus.CANCELED]}, "then": SubscriptionStatus.CANCELED},
          {"case": {"$lte": ["$expires_at", now]}, "then": SubscriptionStatus.EXPIRED},
        ],
        "default": "$status",
      }
    }
    # expires_at null nunca es <= now en Mongo, así que un plan sin caducidad cae al default
    by_status = {s: 0 for s in SubscriptionStatus}
    async for row in self.collection.aggregate(
      [{"$group": {"_id": derived, "count": {"$sum": 1}}}]
    ):
      by_status[row["_id"]] = row["count"]
    trials_expiring = await self.collection.count_documents({
      "status": SubscriptionStatus.TRIALING,
      "expires_at": {"$gt": now, "$lte": now + timedelta(days=7)},
    })
    return {"by_status": by_status, "trials_expiring_7_days": trials_expiring}
