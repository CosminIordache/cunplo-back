from typing import Optional
from bson import ObjectId

from src.domain.user import User
from src.application.ports.user_repository import UserRepository


class UserService:
  def __init__(self, repository: UserRepository):
    self.repository = repository

  async def create(self, user: User) -> User:
    return await self.repository.create(user)

  async def get(self, user_id: ObjectId) -> Optional[User]:
    return await self.repository.get(user_id)

  async def list(self) -> list[User]:
    return await self.repository.list()

  async def update(self, user_id: ObjectId, changes: dict) -> Optional[User]:
    return await self.repository.update(user_id, changes)

  async def delete(self, user_id: ObjectId) -> bool:
    return await self.repository.delete(user_id)
