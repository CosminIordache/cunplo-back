from typing import Optional

from src.domain.user import User
from src.application.use_cases.user_service import EmailAlreadyUsed, UserService
from src.infrastructure.utils.security import create_token, verify_password

__all__ = ["AuthService", "EmailAlreadyUsed", "InvalidCredentials"]


class InvalidCredentials(Exception):
  pass


class AuthService:
  def __init__(self, users: UserService):
    self.users = users

  async def register(self, user: User) -> tuple[User, str]:
    user = await self.users.create(user)
    return user, create_token(str(user.id))

  async def login(self, email: str, password: str) -> tuple[User, str]:
    user = await self.users.get_by_email(email)
    if not user or not user.password or not verify_password(password, user.password):
      raise InvalidCredentials
    return user, create_token(str(user.id))

  async def login_oauth(self, claims: dict) -> tuple[User, str]:
    """Alta o login con los claims del id_token (el email ya viene verificado)."""
    user = await self.users.get_by_email(claims["email"])
    if not user:
      user = await self.users.create(
        User(
          username=claims.get("name") or claims["email"].split("@")[0],
          email=claims["email"],
          password=None, 
          phone=None,
          timezone="UTC",
          language=claims.get("locale", "en").split("-")[0],
        )
      )
    return user, create_token(str(user.id))

  async def current(self, user_id) -> Optional[User]:
    return await self.users.get(user_id)
