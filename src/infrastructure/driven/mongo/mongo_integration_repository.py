from typing import Optional
from dataclasses import asdict
from datetime import datetime, UTC
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.domain.integration import Integration, Provider
from src.infrastructure.utils.crypto import decrypt, encrypt


def _to_document(integration: Integration) -> dict:
  document = asdict(integration)
  document["_id"] = document.pop("id")
  document["refresh_token"] = encrypt(document["refresh_token"])
  document["access_token"] = encrypt(document["access_token"])
  return document


def _to_integration(document: dict) -> Integration:
  document = dict(document)
  document["id"] = document.pop("_id")
  document["refresh_token"] = decrypt(document["refresh_token"])
  document["access_token"] = decrypt(document["access_token"])
  return Integration(**document)


class MongoIntegrationRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["integrations"]

  async def upsert(self, integration: Integration) -> Integration:
    """Una integración por usuario, provider y cuenta: reconectar la misma cuenta
    actualiza, conectar otra distinta inserta una fila más."""
    integration.updated_at = datetime.now(UTC)
    document = _to_document(integration)
    document.pop("_id")
    document.pop("created_at")
    await self.collection.update_one(
      {
        "user_id": integration.user_id,
        "provider": integration.provider,
        "account_id": integration.account_id,
      },
      {"$set": document, "$setOnInsert": {"_id": integration.id, "created_at": integration.created_at}},
      upsert=True,
    )
    return integration

  async def get(self, integration_id: ObjectId, user_id: ObjectId) -> Optional[Integration]:
    # user_id en el filtro: nadie lee integraciones de otro
    doc = await self.collection.find_one({"_id": integration_id, "user_id": user_id})
    return _to_integration(doc) if doc else None

  async def get_by_user_account(
    self, user_id: ObjectId, provider: Provider, account_id: str
  ) -> Optional[Integration]:
    doc = await self.collection.find_one(
      {"user_id": user_id, "provider": provider, "account_id": account_id}
    )
    return _to_integration(doc) if doc else None

  async def get_by_email(self, provider: Provider, email: str) -> Optional[Integration]:
    doc = await self.collection.find_one({"provider": provider, "email": email})
    return _to_integration(doc) if doc else None

  async def get_by_subscription(self, subscription_id: str) -> Optional[Integration]:
    # el webhook de Graph solo trae el id de la subscription: es la única entrada
    doc = await self.collection.find_one({"subscription_id": subscription_id})
    return _to_integration(doc) if doc else None

  async def list_by_user(
    self, user_id: ObjectId, provider: Optional[Provider] = None
  ) -> list[Integration]:
    query = {"user_id": user_id}
    if provider:
      query["provider"] = provider
    return [_to_integration(d) async for d in self.collection.find(query)]

  async def list_expiring(self, provider: Provider, before: datetime) -> list[Integration]:
    """Las que caducan antes de 'before', y las que nunca han tenido push."""
    query = {
      "provider": provider,
      "$or": [{"watch_expires_at": {"$lt": before}}, {"watch_expires_at": None}],
    }
    return [_to_integration(d) async for d in self.collection.find(query)]

  async def delete(self, integration_id: ObjectId, user_id: ObjectId) -> bool:
    # user_id en el filtro: nadie borra integraciones de otro
    result = await self.collection.delete_one({"_id": integration_id, "user_id": user_id})
    return result.deleted_count == 1
