from dataclasses import dataclass, field
from bson import ObjectId
from datetime import datetime, UTC
from enum import StrEnum
from typing import Optional
from pydantic import EmailStr
from pydantic_extra_types.timezone_name import TimeZoneName
from pydantic_extra_types.language_code import LanguageAlpha2


class AuthProvider(StrEnum):
  GOOGLE = "google"
  MICROSOFT = "microsoft"


@dataclass
class User:
  username: str
  email: EmailStr
  password: Optional[str]
  phone: Optional[str]
  timezone: TimeZoneName
  language: LanguageAlpha2

  # con quién entra: el 'sub' es la identidad, el email puede cambiar o repetirse
  auth_provider: Optional[AuthProvider] = None
  auth_account_id: Optional[str] = None

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))