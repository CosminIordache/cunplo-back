from dataclasses import asdict
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from src.domain.message import Message


def _to_document(message: Message) -> dict:
  document = asdict(message)
  document["_id"] = document.pop("id")
  return document


def _to_message(document: dict) -> Message:
  document = dict(document)
  document["id"] = document.pop("_id")
  return Message(**document)


class MongoMessageRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["messages"]

  async def upsert(self, message: Message) -> Message:
    document = _to_document(message)
    document.pop("_id")
    document.pop("created_at")
    # devuelve el documento real: si ya existía, el _id es el suyo y no el recién
    # generado, y los adjuntos cuelgan de ese id
    updated = await self.collection.find_one_and_update(
      {"integration_id": message.integration_id, "provider_id": message.provider_id},
      {"$set": document, "$setOnInsert": {"_id": message.id, "created_at": message.created_at}},
      upsert=True,
      return_document=ReturnDocument.AFTER,
    )
    return _to_message(updated)

  async def list_by_thread_id_user_id(
    self, user_id: ObjectId, integration_id: ObjectId, thread_id: str
  ) -> list[Message]:
    # la cuenta acota el hilo: el mismo thread_id puede existir en dos buzones
    cursor = self.collection.find(
      {"user_id": user_id, "integration_id": integration_id, "thread_id": thread_id}
    )
    return [_to_message(d) async for d in cursor.sort("internal_date", 1)]

  async def delete(self, message_id: ObjectId, user_id: ObjectId) -> bool:
    result = await self.collection.delete_one({"_id": message_id, "user_id": user_id})
    return result.deleted_count == 1

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    result = await self.collection.delete_many({"user_id": user_id})
    return result.deleted_count

  async def delete_by_thread(
    self, user_id: ObjectId, integration_id: ObjectId, thread_id: str
  ) -> int:
    result = await self.collection.delete_many(
      {"user_id": user_id, "integration_id": integration_id, "thread_id": thread_id}
    )
    return result.deleted_count
