from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from typing import Optional, List

from bson import ObjectId


class Status(StrEnum):
  TO_VALIDATE = "to_validate"
  TODO= "todo"
  WAITING_RESPONSE = "waiting_response"
  DONE = "done"


@dataclass
class Task:

  user_id: ObjectId
  thread_id: str
  title: str
  status: Status
 
  contact_ids: List[ObjectId] = field(default_factory=list)
  due_at: Optional[datetime] = None  # None cuando el correo no dice fecha

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
