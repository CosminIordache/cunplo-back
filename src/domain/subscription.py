from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from enum import StrEnum
from typing import Optional

from bson import ObjectId


class Plan(StrEnum):
  PRO = "pro"


class SubscriptionStatus(StrEnum):
  TRIALING = "trialing"
  ACTIVE = "active"
  EXPIRED = "expired"
  CANCELED = "canceled"


TRIAL_DAYS = 30


@dataclass
class Subscription:

  user_id: ObjectId

  plan: Plan = Plan.PRO
  status: SubscriptionStatus = SubscriptionStatus.TRIALING

  # cuándo caduca lo que tiene ahora: fin del trial o fin del periodo pagado.
  # None = sin caducidad (un plan de por vida, o una cuenta interna)
  expires_at: Optional[datetime] = field(
    default_factory=lambda: datetime.now(UTC) + timedelta(days=TRIAL_DAYS)
  )

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

  @property
  def expired(self) -> bool:
    if self.expires_at is None:
      return False  # sin caducidad
    # Mongo devuelve datetimes naive en UTC
    expires = self.expires_at
    if expires.tzinfo is None:
      expires = expires.replace(tzinfo=UTC)
    return expires <= datetime.now(UTC)

  @property
  def current_status(self) -> SubscriptionStatus:
    """El status de verdad. El guardado se queda viejo en cuanto pasa la fecha,
    así que se deriva aquí en vez de con un cron que expire filas. Un CANCELED
    sigue siendo CANCELED: dice por qué no se renovó, no si aún queda acceso."""
    if self.status is SubscriptionStatus.CANCELED:
      return self.status
    return SubscriptionStatus.EXPIRED if self.expired else self.status

  @property
  def is_active(self) -> bool:
    """Si da acceso ahora mismo. Un cancelado sigue entrando hasta que caduque
    lo que ya pagó."""
    if self.status is SubscriptionStatus.EXPIRED:
      return False
    # cancelar un plan sin caducidad corta ya: no hay periodo pagado que agotar
    if self.status is SubscriptionStatus.CANCELED and self.expires_at is None:
      return False
    return not self.expired
