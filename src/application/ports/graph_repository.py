from typing import Protocol
from bson import ObjectId


class GraphRepository(Protocol):
  async def build(self, user_id: ObjectId) -> dict: ...
