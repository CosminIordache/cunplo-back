from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator


class UsageUsersOut(BaseModel):
  user_id: Annotated[str, BeforeValidator(str)]
  email: Optional[str] = None  # null en las filas anteriores a guardar el email
  runs: int
  input_tokens: int
  output_tokens: int
  cache_read_tokens: int
  reasoning_tokens: int
  cost: Decimal


class UsageAllOut(BaseModel):
  """El total de la casa, sin desglosar por usuario."""

  runs: int
  input_tokens: int
  output_tokens: int
  cache_read_tokens: int
  reasoning_tokens: int
  cost: Decimal
