from typing import Optional
from bson import ObjectId

import phonenumbers

from src.domain.contact import Contact
from src.application.ports.contact_repository import ContactRepository


class ContactEmailAlreadyUsed(Exception):
  pass


class ContactPhoneAlreadyUsed(Exception):
  pass


def normalize_phone(raw: Optional[str]) -> Optional[str]:
  """E.164 o None. Un mismo número llega como '+34 600 11 22 33' o '+34600112233' y
  sin normalizar serían dos contactos distintos."""
  # ponytail: sin prefijo internacional no se sabe el país; se descarta en vez de adivinarlo
  try:
    number = phonenumbers.parse(raw or "", None)
  except phonenumbers.NumberParseException:
    return None
  if not phonenumbers.is_possible_number(number):
    return None
  return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


class ContactService:
  def __init__(self, repository: ContactRepository):
    self.repository = repository

  async def create(self, contact: Contact) -> Contact:
    # el email se guarda y se busca siempre en minúsculas: la query de Mongo distingue
    # mayúsculas y el mismo buzón llega escrito de cualquier forma
    if contact.email:
      contact.email = contact.email.lower()
      if await self.repository.get_by_email(contact.user_id, contact.email):
        raise ContactEmailAlreadyUsed
    if contact.phone:
      contact.phone = normalize_phone(contact.phone)
      if contact.phone and await self.repository.get_by_phone(contact.user_id, contact.phone):
        raise ContactPhoneAlreadyUsed
    return await self.repository.create(contact)

  async def get(self, contact_id: ObjectId, user_id: ObjectId) -> Optional[Contact]:
    return await self.repository.get(contact_id, user_id)

  async def get_by_email(self, user_id: ObjectId, email: str) -> Optional[Contact]:
    return await self.repository.get_by_email(user_id, email.lower())

  async def get_by_phone(self, user_id: ObjectId, phone: str) -> Optional[Contact]:
    phone = normalize_phone(phone)
    return await self.repository.get_by_phone(user_id, phone) if phone else None

  async def get_by_user(
    self, user_id: ObjectId, search: Optional[str] = None, skip: int = 0, limit: int = 0
  ) -> list[Contact]:
    return await self.repository.get_by_user(user_id, search, skip, limit)

  async def update(self, contact_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Contact]:
    if changes.get("email"):
      changes["email"] = changes["email"].lower()
      owner = await self.repository.get_by_email(user_id, changes["email"])
      if owner and owner.id != contact_id:
        raise ContactEmailAlreadyUsed
    if changes.get("phone"):
      changes["phone"] = normalize_phone(changes["phone"])
      owner = changes["phone"] and await self.repository.get_by_phone(user_id, changes["phone"])
      if owner and owner.id != contact_id:
        raise ContactPhoneAlreadyUsed
    return await self.repository.update(contact_id, user_id, changes)

  async def delete(self, contact_id: ObjectId, user_id: ObjectId) -> bool:
    return await self.repository.delete(contact_id, user_id)

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    return await self.repository.delete_all_by_user(user_id)
