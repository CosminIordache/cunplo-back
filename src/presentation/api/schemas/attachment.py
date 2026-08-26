from typing import Annotated
from datetime import datetime
from pydantic import BaseModel, BeforeValidator, ConfigDict


class AttachmentOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  message_id: Annotated[str, BeforeValidator(str)]
  integration_id: Annotated[str, BeforeValidator(str)]
  filename: str
  mime_type: str
  size: int
  created_at: datetime
