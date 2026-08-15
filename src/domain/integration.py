from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from typing import Optional

from bson import ObjectId


class Provider(StrEnum):
  GOOGLE = "google"


@dataclass
class Integration:

  user_id: ObjectId
  provider: Provider
  account_id: str  # 'sub' del proveedor: estable aunque el usuario cambie de email
  email: str
  scopes: list[str]
  refresh_token: Optional[str]  # cifrado en el repositorio, nunca en claro en Mongo
  access_token: Optional[str] = None
  expires_at: Optional[datetime] = None
  history_id: Optional[str] = None  # versión del buzón hasta la que hemos procesado
  watch_expires_at: Optional[datetime] = None  # el watch de Gmail caduca a los 7 días

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

  def is_expired(self) -> bool:
    if self.expires_at is None:
      return True
    expires_at = self.expires_at
    if expires_at.tzinfo is None:
      expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)
