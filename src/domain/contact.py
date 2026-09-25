from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional

from bson import ObjectId


@dataclass
class Contact:

  user_id: ObjectId
  # al menos uno de los dos: el correo identifica por email, WhatsApp solo por teléfono
  email: Optional[str] = None
  name: Optional[str] = None
  phone: Optional[str] = None  # E.164 ("+34600112233"), el formato que compara get_by_phone

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
