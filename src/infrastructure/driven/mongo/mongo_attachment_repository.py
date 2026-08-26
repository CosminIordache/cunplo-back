from dataclasses import asdict
from typing import Optional
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.domain.attachment import Attachment


def _to_document(attachment: Attachment) -> dict:
  document = asdict(attachment)
  document["_id"] = document.pop("id")
  return document


def _to_attachment(document: dict) -> Attachment:
  document = dict(document)
  document["id"] = document.pop("_id")
  return Attachment(**document)


class MongoAttachmentRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["attachments"]

  async def upsert(self, attachment: Attachment) -> Attachment:
    document = _to_document(attachment)
    document.pop("_id")
    document.pop("created_at")
    await self.collection.update_one(
      {"message_id": attachment.message_id, "attachment_id": attachment.attachment_id},
      {"$set": document, "$setOnInsert": {"_id": attachment.id, "created_at": attachment.created_at}},
      upsert=True,
    )
    return attachment

  async def get(self, attachment_id: ObjectId, user_id: ObjectId) -> Optional[Attachment]:
    document = await self.collection.find_one({"_id": attachment_id, "user_id": user_id})
    return _to_attachment(document) if document else None

  async def list_by_messages(self, user_id: ObjectId, message_ids: list[ObjectId]) -> list[Attachment]:
    """Todos los adjuntos de un lote de mensajes: el hilo se resuelve en una consulta."""
    if not message_ids:
      return []
    cursor = self.collection.find({"user_id": user_id, "message_id": {"$in": message_ids}})
    return [_to_attachment(d) async for d in cursor]

  async def delete_by_messages(self, user_id: ObjectId, message_ids: list[ObjectId]) -> list[str]:
    """Borra los adjuntos de esos mensajes y devuelve sus claves, que siguen vivas
    en el bucket hasta que alguien las borre."""
    if not message_ids:
      return []
    filter = {"user_id": user_id, "message_id": {"$in": message_ids}}
    keys = [d["storage_key"] async for d in self.collection.find(filter, {"storage_key": 1})]
    await self.collection.delete_many(filter)
    return keys

  async def delete_all_by_user(self, user_id: ObjectId) -> list[str]:
    keys = [
      d["storage_key"]
      async for d in self.collection.find({"user_id": user_id}, {"storage_key": 1})
    ]
    await self.collection.delete_many({"user_id": user_id})
    return keys
