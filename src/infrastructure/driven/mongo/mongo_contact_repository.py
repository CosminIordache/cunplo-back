from typing import Optional
from dataclasses import asdict
from datetime import datetime, UTC
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.domain.contact import Contact


def _to_document(contact: Contact) -> dict:
  document = asdict(contact)
  document["_id"] = document.pop("id")
  return document


def _to_contact(document: dict) -> Contact:
  document = dict(document)
  document["id"] = document.pop("_id")
  return Contact(**document)


class MongoContactRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["contacts"]

  async def create(self, contact: Contact) -> Contact:
    await self.collection.insert_one(_to_document(contact))
    return contact

  async def get(self, contact_id: ObjectId, user_id: ObjectId) -> Optional[Contact]:
    # user_id en el filtro: nadie lee el contacto de otro
    doc = await self.collection.find_one({"_id": contact_id, "user_id": user_id})
    return _to_contact(doc) if doc else None

  async def get_by_email(self, user_id: ObjectId, email: str) -> Optional[Contact]:
    """Un contacto por usuario y email: esta es la consulta que evita duplicarlo."""
    doc = await self.collection.find_one({"user_id": user_id, "email": email})
    return _to_contact(doc) if doc else None

  async def get_by_user(self, user_id: ObjectId) -> list[Contact]:
    # user_id en el filtro: nadie lee los contactos de otro
    cursor = self.collection.find({"user_id": user_id})
    return [_to_contact(d) async for d in cursor.sort("name", 1)]

  async def update(self, contact_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Contact]:
    changes["updated_at"] = datetime.now(UTC)
    doc = await self.collection.find_one_and_update(
      {"_id": contact_id, "user_id": user_id}, {"$set": changes}, return_document=True
    )
    return _to_contact(doc) if doc else None

  async def delete(self, contact_id: ObjectId, user_id: ObjectId) -> bool:
    result = await self.collection.delete_one({"_id": contact_id, "user_id": user_id})
    return result.deleted_count == 1
