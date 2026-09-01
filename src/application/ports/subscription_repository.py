from typing import Optional, Protocol

from bson import ObjectId

from src.domain.subscription import Subscription


class SubscriptionRepository(Protocol):
  async def create(self, subscription: Subscription) -> Subscription: ...
  async def get_by_user(self, user_id: ObjectId) -> Optional[Subscription]: ...
  async def update(
    self, user_id: ObjectId, changes: dict
  ) -> Optional[Subscription]: ...
  async def delete_by_user(self, user_id: ObjectId) -> bool: ...
