from typing import Protocol
from bson import ObjectId

from src.domain.usage import Usage


class UsageRepository(Protocol):
  async def create(self, usage: Usage) -> Usage: ...
  async def total_by_user(self, user_id: ObjectId) -> dict: ...
  async def delete_all_by_user(self, user_id: ObjectId) -> int: ...
