from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional

from bson import ObjectId


@dataclass
class Contact:

  user_id: ObjectId
  email: str
  name: Optional[str] = None
  phone: Optional[str] = None

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
