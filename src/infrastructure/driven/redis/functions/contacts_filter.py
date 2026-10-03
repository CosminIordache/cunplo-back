from email.utils import parseaddr

import logfire

from src.application.use_cases.contact_service import (
  ContactEmailAlreadyUsed,
  ContactPhoneAlreadyUsed,
  normalize_phone,
)
from src.domain.contact import Contact


async def is_known_contact(ctx, user_id, sender: str) -> bool:
  """El remitente ('Nombre <a@b.com>' o 'a@b.com') ya es contacto del usuario."""
  # ponytail: solo mira el remitente, no To/Cc; amplía si hace falta filtrar por destinatario
  address = parseaddr(sender)[1]
  return bool(address) and bool(await ctx["contact_service"].get_by_email(user_id, address))


async def _find(ctx, user_id, email, phone):
  if email:
    return await ctx["contact_service"].get_by_email(user_id, email)
  return await ctx["contact_service"].get_by_phone(user_id, phone)


async def resolve_contacts(ctx, user_id, own_address: str, extracted_contacts) -> list:
  """Los contactos del agente a ids: crea los que no existen todavía. Se buscan por
  email y, si no traen, por teléfono. own_address es el email del dueño de la cuenta."""
  own = own_address.lower()
  contact_ids = []
  for extracted in extracted_contacts:
    email = (extracted.email or "").lower() or None
    phone = normalize_phone(extracted.phone)
    # el usuario no es contacto de sí mismo, lo diga el agente o no
    if own in (email, phone):
      continue
    if not (email or phone):
      continue  # sin nada con qué buscarlo no hay contacto

    contact = await _find(ctx, user_id, email, phone) or await _create(
      ctx, Contact(user_id=user_id, email=email, name=extracted.name, phone=phone)
    )
    if contact:
      contact_ids.append(contact.id)
  return contact_ids


async def _create(ctx, contact: Contact):
  try:
    created = await ctx["contact_service"].create(contact)
  except ContactEmailAlreadyUsed:
    # carrera con otro job del mismo hilo: el que perdió vuelve a leerlo
    return await _find(ctx, contact.user_id, contact.email, None)
  except ContactPhoneAlreadyUsed:
    if not contact.email:
      return await _find(ctx, contact.user_id, None, contact.phone)  # misma carrera, contacto sin email
    # el teléfono ya es de otro contacto (una centralita): este se crea sin él
    contact.phone = None
    return await _create(ctx, contact)
  logfire.info(
    "Contact {email} {phone} created for user {user_id}",
    email=created.email,
    phone=created.phone,
    user_id=created.user_id,
  )
  return created
