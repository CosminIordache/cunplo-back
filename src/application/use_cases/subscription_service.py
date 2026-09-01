from datetime import datetime, timedelta, UTC
from typing import Optional

from bson import ObjectId

from src.application.ports.subscription_repository import SubscriptionRepository
from src.domain.subscription import Plan, Subscription, SubscriptionStatus


class SubscriptionService:
  def __init__(self, repository: SubscriptionRepository):
    self.repository = repository

  async def start_trial(self, user_id: ObjectId) -> Subscription:
    """Al registrarse: free con 14 días. Idempotente, un usuario no puede
    estrenar el trial dos veces volviendo a entrar con otro proveedor."""
    existing = await self.repository.get_by_user(user_id)
    if existing:
      return existing
    return await self.repository.create(Subscription(user_id=user_id))

  async def get_by_user(self, user_id: ObjectId) -> Optional[Subscription]:
    return await self.repository.get_by_user(user_id)

  async def set_plan(
    self, user_id: ObjectId, plan: Plan, days: Optional[int] = None
  ) -> Optional[Subscription]:
    """Sube o baja de plan. `days` None deja el acceso sin caducidad; cuando
    entre la pasarela, aquí se pondrá el fin del periodo que ella diga."""
    expires_at = datetime.now(UTC) + timedelta(days=days) if days else None
    return await self.repository.update(
      user_id,
      {
        "plan": plan,
        "status": SubscriptionStatus.ACTIVE,
        "expires_at": expires_at,
      },
    )

  async def cancel(self, user_id: ObjectId) -> Optional[Subscription]:
    """Cancela sin cortar: sigue activo hasta expires_at, que es lo pagado."""
    return await self.repository.update(
      user_id, {"status": SubscriptionStatus.CANCELED}
    )

  async def delete_by_user(self, user_id: ObjectId) -> bool:
    return await self.repository.delete_by_user(user_id)
