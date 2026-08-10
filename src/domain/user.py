from dataclasses import dataclass, field
from bson import ObjectId
from datetime import datetime, UTC
from typing import Optional
from pydantic import EmailStr
from pydantic_extra_types.timezone_name import TimeZoneName
from pydantic_extra_types.language_code import LanguageAlpha2


@dataclass
class User:
  username: str
  email: EmailStr
  password: Optional[str]
  phone: str
  timezone: TimeZoneName
  language: LanguageAlpha2

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))