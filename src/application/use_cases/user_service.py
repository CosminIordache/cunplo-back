from typing import Optional
from bson import ObjectId

from src.domain.user import AuthProvider, User
from src.application.ports.user_repository import UserRepository
from src.infrastructure.utils.security import hash_password


class EmailAlreadyUsed(Exception):
  pass


class UserService:
  def __init__(self, repository: UserRepository):
    self.repository = repository

  async def create(self, user: User) -> User:
    if await self.repository.get_by_email(user.email):
      raise EmailAlreadyUsed
    if user.password:
      user.password = hash_password(user.password)
    return await self.repository.create(user)

  async def get(self, user_id: ObjectId) -> Optional[User]:
    return await self.repository.get(user_id)

  async def get_by_email(self, email: str) -> Optional[User]:
    return await self.repository.get_by_email(email)

  async def get_by_auth_account(
    self, auth_provider: AuthProvider, auth_account_id: str
  ) -> Optional[User]:
    return await self.repository.get_by_auth_account(auth_provider, auth_account_id)

  async def list(self) -> list[User]:
    return await self.repository.list()

  async def update(self, user_id: ObjectId, changes: dict) -> Optional[User]:
    if changes.get("password"):
      changes["password"] = hash_password(changes["password"])
    if "email" in changes:
      owner = await self.repository.get_by_email(changes["email"])
      if owner and owner.id != user_id:
        raise EmailAlreadyUsed
    return await self.repository.update(user_id, changes)

  async def delete(self, user_id: ObjectId) -> bool:
    return await self.repository.delete(user_id)
