from dataclasses import asdict
from datetime import datetime, UTC
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from src.domain.organization import Invitation, Organization
from src.domain.user import User
from src.infrastructure.driven.mongo.mongo_user_repository import _to_user


def _to_document(entity: Organization | Invitation) -> dict:
  document = asdict(entity)
  document["_id"] = document.pop("id")
  return document


def _to_organization(document: dict) -> Organization:
  document = dict(document)
  document["id"] = document.pop("_id")
  return Organization(**document)


def _to_invitation(document: dict) -> Invitation:
  document = dict(document)
  document["id"] = document.pop("_id")
  # ponytail: las de antes llevaban rol; caducan solas en 7 días, quitar después
  document.pop("role", None)
  return Invitation(**document)


class MongoOrganizationRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["organizations"]
    self.users = db["users"]  # la pertenencia vive en el usuario

  async def create(self, organization: Organization) -> Organization:
    await self.collection.insert_one(_to_document(organization))
    return organization

  async def get(self, organization_id: ObjectId) -> Optional[Organization]:
    document = await self.collection.find_one({"_id": organization_id})
    return _to_organization(document) if document else None

  async def update(self, organization_id: ObjectId, changes: dict) -> Optional[Organization]:
    changes = {**changes, "updated_at": datetime.now(UTC)}
    document = await self.collection.find_one_and_update(
      {"_id": organization_id}, {"$set": changes}, return_document=ReturnDocument.AFTER
    )
    return _to_organization(document) if document else None

  async def delete(self, organization_id: ObjectId) -> bool:
    result = await self.collection.delete_one({"_id": organization_id})
    return result.deleted_count == 1

  async def list_members(self, organization_id: ObjectId) -> list[User]:
    query = {"organization_id": organization_id}
    return [_to_user(d) async for d in self.users.find(query).sort("created_at", 1)]


class MongoInvitationRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["invitations"]

  async def upsert(self, invitation: Invitation) -> Invitation:
    """Una por (organización, email): reinvitar sustituye la anterior y renueva la caducidad."""
    document = _to_document(invitation)
    document.pop("_id")
    saved = await self.collection.find_one_and_update(
      {"organization_id": invitation.organization_id, "email": invitation.email},
      {"$set": document, "$setOnInsert": {"_id": invitation.id}},
      upsert=True,
      return_document=ReturnDocument.AFTER,
    )
    return _to_invitation(saved)

  async def get_for_email(self, invitation_id: ObjectId, email: str) -> Optional[Invitation]:
    # el TTL de Mongo pasa cada minuto: la caducidad se filtra también aquí
    document = await self.collection.find_one(
      {"_id": invitation_id, "email": email, "expires_at": {"$gt": datetime.now(UTC)}}
    )
    return _to_invitation(document) if document else None

  async def list_by_org(self, organization_id: ObjectId) -> list[Invitation]:
    query = {"organization_id": organization_id, "expires_at": {"$gt": datetime.now(UTC)}}
    return [_to_invitation(d) async for d in self.collection.find(query).sort("created_at", -1)]

  async def list_by_email(self, email: str) -> list[Invitation]:
    query = {"email": email, "expires_at": {"$gt": datetime.now(UTC)}}
    return [_to_invitation(d) async for d in self.collection.find(query).sort("created_at", -1)]

  async def delete(self, invitation_id: ObjectId, filter: dict) -> bool:
    """`filter` acota quién puede borrarla: la organización que invita o el email invitado."""
    result = await self.collection.delete_one({**filter, "_id": invitation_id})
    return result.deleted_count == 1

  async def delete_by_org(self, organization_id: ObjectId) -> None:
    await self.collection.delete_many({"organization_id": organization_id})
