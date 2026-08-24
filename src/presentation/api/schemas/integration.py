from typing import Annotated
from datetime import datetime

from pydantic import BaseModel, BeforeValidator, ConfigDict


class IntegrationOut(BaseModel):
  """Nunca expone tokens: solo qué cuenta está conectada y con qué permisos."""

  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]
  provider: str
  account_id: str
  email: str
  scopes: list[str]
  created_at: datetime
  updated_at: datetime
