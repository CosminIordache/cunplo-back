from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional

from bson import ObjectId


@dataclass
class Message:

  user_id: ObjectId
  integration_id: ObjectId
  provider_id: str  # id del mensaje en Gmail: único por cuenta, no global
  thread_id: str
  
  sender: str  # cabecera 'From' cruda: "Nombre <email@dominio>"
  to: str
  subject: str
  body: str
  internal_date: int  # epoch ms de Gmail: el orden real dentro del hilo

  cc: Optional[str] = None  # la mayoría de correos no llevan copia
  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
