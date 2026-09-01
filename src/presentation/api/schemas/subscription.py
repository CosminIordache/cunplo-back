from typing import Annotated, Optional
from datetime import datetime

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from src.domain.subscription import Plan, SubscriptionStatus


class SubscriptionOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  user_id: Annotated[str, BeforeValidator(str)]
  plan: Plan
  # el derivado, no el guardado: ese se queda viejo al pasar expires_at
  status: Annotated[SubscriptionStatus, Field(validation_alias="current_status")]
  expires_at: Optional[datetime] = None
  is_active: bool
  created_at: datetime
  updated_at: datetime


class PlanUpdate(BaseModel):
  plan: Plan
  days: Optional[int] = None  # None: sin caducidad
