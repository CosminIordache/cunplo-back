from bson import ObjectId

from src.application.ports.graph_repository import GraphRepository


class GraphService:
  def __init__(self, repository: GraphRepository):
    self.repository = repository

  async def build(self, user_id: ObjectId) -> dict:
    return await self.repository.build(user_id)
