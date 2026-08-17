from typing import Annotated, Optional
from datetime import datetime
from pydantic import BaseModel, BeforeValidator, ConfigDict


class MessageOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  user_id: Annotated[str, BeforeValidator(str)]
  integration_id: Annotated[str, BeforeValidator(str)]
  provider_id: str
  thread_id: str
  sender: str
  to: str
  cc: Optional[str] = None
  subject: str
  body: str
  internal_date: int
  created_at: datetime
