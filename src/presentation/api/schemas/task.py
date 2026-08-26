from typing import Annotated, List, Optional
from datetime import datetime
from pydantic import BaseModel, BeforeValidator, ConfigDict

from src.domain.task import Status


class TaskUpdate(BaseModel):
  title: Optional[str] = None
  status: Optional[Status] = None
  due_at: Optional[datetime] = None


class TaskOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  user_id: Annotated[str, BeforeValidator(str)]
  integration_id: Annotated[str, BeforeValidator(str)]
  thread_id: str
  title: str
  status: Status
  contact_ids: List[Annotated[str, BeforeValidator(str)]] = []
  due_at: Optional[datetime] = None
  created_at: datetime
  updated_at: datetime
