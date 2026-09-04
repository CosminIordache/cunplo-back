from typing import AsyncIterator

from bson import ObjectId

from src.application.ports.graph_repository import GraphRepository


class GraphService:
  def __init__(self, repository: GraphRepository):
    self.repository = repository

  def stream(self, user_id: ObjectId) -> AsyncIterator[bytes]:
    return self.repository.stream(user_id)
