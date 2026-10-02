from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from typing import Optional

from bson import ObjectId


class InvalidIntegration(ValueError):
  pass


class Provider(StrEnum):
  GOOGLE = "google"
  WHATSAPP = "whatsapp"


@dataclass
class Integration:

  user_id: ObjectId
  provider: Provider
  account_id: str  # 'sub' del proveedor (en WhatsApp, el device_id de GOWA): estable aunque cambie el email
  scopes: list[str]
  refresh_token: Optional[str]  # cifrado en el repositorio, nunca en claro en Mongo
  email: Optional[str] = None  # solo correo (Google)
  phone: Optional[str] = None  # solo WhatsApp, en E.164
  access_token: Optional[str] = None
  expires_at: Optional[datetime] = None
  history_id: Optional[str] = None  # versión del buzón hasta la que hemos procesado
  watch_expires_at: Optional[datetime] = None  # Gmail caduca a los 7 días

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

  def __post_init__(self):
    # un canal, un identificador: el correo lleva email y WhatsApp lleva teléfono, nunca ambos
    if self.provider == Provider.WHATSAPP:
      if not (self.phone and self.phone[0] == "+" and self.phone[1:].isdigit()):
        raise InvalidIntegration(f"WhatsApp integration needs an E.164 phone, got {self.phone!r}")
      if self.email is not None:
        raise InvalidIntegration("WhatsApp integration cannot have an email")
    else:
      if not self.email:
        raise InvalidIntegration(f"{self.provider} integration needs an email")
      if self.phone is not None:
        raise InvalidIntegration(f"{self.provider} integration cannot have a phone")

  def is_expired(self) -> bool:
    if self.expires_at is None:
      return True
    expires_at = self.expires_at
    if expires_at.tzinfo is None:
      expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)
