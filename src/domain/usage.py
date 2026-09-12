from dataclasses import dataclass, field
from datetime import datetime, UTC
from decimal import Decimal
from enum import StrEnum
from typing import Optional

from bson import ObjectId


class UsageKind(StrEnum):
  """Qué gastó: cada valor es una línea aparte en el desglose de costes."""

  TASK = "task"  # extracción de tareas del correo
  ASSISTANT = "assistant"  # el chat
  TRANSCRIPTION = "transcription"  # voz a texto en directo, por minuto


@dataclass
class Usage:
  """Una llamada al LLM con su coste, para imputársela a un usuario."""

  user_id: ObjectId
  # desnormalizado a propósito: si el usuario borra su cuenta el user_id deja de
  # resolver, y el gasto ya facturado tiene que seguir teniendo nombre
  email: Optional[str]
  model: str  # el id real ejecutado, no el configurado: sale de ModelResponse.model_name
  kind: UsageKind

  input_tokens: int
  output_tokens: int

  requests: int  # llamadas a la API dentro de la run: >1 cuando haya tool calls
  tool_calls: int

  # va dentro de input_tokens, no se suma aparte: solo dice a qué precio salió
  cache_read_tokens: int

  # se factura como output y ya viene incluido en output_tokens
  reasoning_tokens: int

  cost: Optional[Decimal]  # None si genai-prices no conoce el modelo, distinto de 0

  seconds: float = 0.0  # solo transcripción: audio enviado, es lo que se factura

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
