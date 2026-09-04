from typing import AsyncIterator, Protocol
from bson import ObjectId


class GraphRepository(Protocol):
  def stream(self, user_id: ObjectId) -> AsyncIterator[bytes]: ...
