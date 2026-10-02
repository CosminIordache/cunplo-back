from dataclasses import asdict
from email.utils import getaddresses

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from src.domain.message import Message


def participants(sender: str, to: str, cc: str | None) -> list[str]:
  """Los emails de From, To y Cc en minúscula: con quién va el correo, sea del hilo que sea.
  Solo existe en Mongo, para buscar el contexto por interlocutor; el dominio no lo lleva."""
  return sorted({a.lower() for _, a in getaddresses([sender, to or "", cc or ""]) if "@" in a})


def _to_document(message: Message) -> dict:
  document = asdict(message)
  document["_id"] = document.pop("id")
  document["participants"] = participants(message.sender, message.to, message.cc)
  return document


def _to_message(document: dict) -> Message:
  document = dict(document)
  document["id"] = document.pop("_id")
  document.pop("participants", None)
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

  async def list_context(
    self,
    user_id: ObjectId,
    integration_id: ObjectId,
    thread_id: str,
    addresses: list[str],
    exclude_provider_id: str,
    limit: int,
  ) -> list[Message]:
    """Lo último hablado con estas personas: su hilo y cualquier otro en el que estén.
    Los `limit` más recientes, devueltos en orden cronológico."""
    cursor = self.collection.find(
      {
        "user_id": user_id,
        "integration_id": integration_id,
        "provider_id": {"$ne": exclude_provider_id},
        "$or": [{"thread_id": thread_id}, {"participants": {"$in": addresses}}],
      }
    )
    recent = [_to_message(d) async for d in cursor.sort("internal_date", -1).limit(limit)]
    return recent[::-1]

  async def delete(self, message_id: ObjectId, user_id: ObjectId) -> bool:
    result = await self.collection.delete_one({"_id": message_id, "user_id": user_id})
    return result.deleted_count == 1

  async def delete_many(self, user_id: ObjectId, message_ids: list[ObjectId]) -> int:
    result = await self.collection.delete_many({"_id": {"$in": message_ids}, "user_id": user_id})
    return result.deleted_count

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    result = await self.collection.delete_many({"user_id": user_id})
    return result.deleted_count
