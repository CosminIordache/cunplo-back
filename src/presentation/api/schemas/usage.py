from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator


class UsageKindOut(BaseModel):
  """Una línea del desglose: task, assistant, transcription o unknown (filas viejas)."""

  runs: int
  input_tokens: int
  output_tokens: int
  seconds: float  # solo suma en transcription
  cost: Decimal


class UsageAllOut(BaseModel):
  """El total de la casa, sin desglosar por usuario."""

  runs: int
  input_tokens: int
  output_tokens: int
  cache_read_tokens: int
  reasoning_tokens: int
  seconds: float
  cost: Decimal
  by_kind: dict[str, UsageKindOut]


class UsageUsersOut(UsageAllOut):
  user_id: Annotated[str, BeforeValidator(str)]
  email: Optional[str] = None  # null en las filas anteriores a guardar el email
