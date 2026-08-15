from typing import Optional
from bson import ObjectId

from src.domain.contact import Contact
from src.application.ports.contact_repository import ContactRepository


class ContactEmailAlreadyUsed(Exception):
  pass


class ContactService:
  def __init__(self, repository: ContactRepository):
    self.repository = repository

  async def create(self, contact: Contact) -> Contact:
    if await self.repository.get_by_email(contact.user_id, contact.email):
      raise ContactEmailAlreadyUsed
    return await self.repository.create(contact)

  async def get(self, contact_id: ObjectId, user_id: ObjectId) -> Optional[Contact]:
    return await self.repository.get(contact_id, user_id)

  async def get_by_email(self, user_id: ObjectId, email: str) -> Optional[Contact]:
    return await self.repository.get_by_email(user_id, email)

  async def get_by_user(self, user_id: ObjectId) -> list[Contact]:
    return await self.repository.get_by_user(user_id)

  async def update(self, contact_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Contact]:
    if "email" in changes:
      owner = await self.repository.get_by_email(user_id, changes["email"])
      if owner and owner.id != contact_id:
        raise ContactEmailAlreadyUsed
    return await self.repository.update(contact_id, user_id, changes)

  async def delete(self, contact_id: ObjectId, user_id: ObjectId) -> bool:
    return await self.repository.delete(contact_id, user_id)
