from decimal import Decimal

from bson import ObjectId

from src.application.ports.usage_repository import UsageRepository
from src.domain.usage import Usage, UsageKind


class UsageService:
  def __init__(self, repository: UsageRepository):
    self.repository = repository

  async def record(
    self, user_id: ObjectId, email: str, model: str, kind: UsageKind, result
  ) -> Usage:
    """Apunta lo que costó una run del agente. `result` es el AgentRunResult
    de pydantic-ai: aquí es donde se traduce a dominio, y en ningún otro sitio."""
    usage = result.usage
    return await self.repository.create(
      Usage(
        user_id=user_id,
        email=email,
        model=model,
        kind=kind,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        requests=usage.requests,
        tool_calls=usage.tool_calls,
        cache_read_tokens=usage.cache_read_tokens,
        # el pensamiento no es campo propio de RunUsage, viaja dentro de details
        reasoning_tokens=usage.details.get("reasoning_tokens", 0),
        cost=usage.cost,  # None si genai-prices no conoce el modelo
      )
    )

  async def record_transcription(
    self,
    user_id: ObjectId,
    email: str,
    model: str,
    seconds: float,
    input_tokens: int,
    output_tokens: int,
    price_per_minute: Decimal,
  ) -> Usage:
    """Transcripción en directo: los segundos son los que reporta la sesión por frase
    y son lo que se factura. Los tokens solo vienen si el modelo factura así."""
    return await self.repository.create(
      Usage(
        user_id=user_id,
        email=email,
        model=model,
        kind=UsageKind.TRANSCRIPTION,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        requests=1,
        tool_calls=0,
        cache_read_tokens=0,
        reasoning_tokens=0,
        cost=Decimal(seconds) / 60 * price_per_minute,
        seconds=seconds,
      )
    )

  async def total_by_user(self, user_id: ObjectId) -> dict:
    return await self.repository.total_by_user(user_id)

  async def totals_by_user(self) -> list[dict]:
    return await self.repository.totals_by_user()

  async def total_all_users(self) -> dict:
    return await self.repository.total_all_users()

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    return await self.repository.delete_all_by_user(user_id)
