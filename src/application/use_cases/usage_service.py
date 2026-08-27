from bson import ObjectId

from src.application.ports.usage_repository import UsageRepository
from src.domain.usage import Usage


class UsageService:
  def __init__(self, repository: UsageRepository):
    self.repository = repository

  async def record(self, user_id: ObjectId, model: str, result) -> Usage:
    """Apunta lo que costó una run del agente. `result` es el AgentRunResult
    de pydantic-ai: aquí es donde se traduce a dominio, y en ningún otro sitio."""
    usage = result.usage
    return await self.repository.create(
      Usage(
        user_id=user_id,
        model=model,
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

  async def total_by_user(self, user_id: ObjectId) -> dict:
    return await self.repository.total_by_user(user_id)

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    return await self.repository.delete_all_by_user(user_id)
