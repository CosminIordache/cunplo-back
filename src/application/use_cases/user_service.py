from typing import Optional
from bson import ObjectId

from src.domain.user import AuthProvider, User
from src.application.ports.storage import Storage
from src.application.ports.user_repository import UserRepository
from src.infrastructure.utils.security import hash_password


class EmailAlreadyUsed(Exception):
  pass


class UserService:
  def __init__(self, repository: UserRepository, storage: Storage):
    self.repository = repository
    self.storage = storage

  async def create(self, user: User) -> User:
    if await self.repository.get_by_email(user.email):
      raise EmailAlreadyUsed
    if user.password:
      user.password = hash_password(user.password)
    return await self.repository.create(user)

  async def get(self, user_id: ObjectId) -> Optional[User]:
    user = await self.repository.get(user_id)
    return await self.sign_picture(user) if user else None

  async def get_by_email(self, email: str) -> Optional[User]:
    return await self.repository.get_by_email(email)

  async def get_by_auth_account(
    self, auth_provider: AuthProvider, auth_account_id: str
  ) -> Optional[User]:
    return await self.repository.get_by_auth_account(auth_provider, auth_account_id)

  async def list(self) -> list[User]:
    return [await self.sign_picture(u) for u in await self.repository.list()]

  async def update(self, user_id: ObjectId, changes: dict) -> Optional[User]:
    if changes.get("password"):
      changes["password"] = hash_password(changes["password"])
    if "email" in changes:
      owner = await self.repository.get_by_email(changes["email"])
      if owner and owner.id != user_id:
        raise EmailAlreadyUsed
    user = await self.repository.update(user_id, changes)
    return await self.sign_picture(user) if user else None

  async def set_picture(
    self, user_id: ObjectId, data: bytes, content_type: str
  ) -> Optional[User]:
    """Sube la foto al bucket y guarda la clave en 'picture'.

    Clave fija por usuario: sustituir la foto pisa la anterior y no deja basura
    que limpiar. Si lo que había era la URL del proveedor, simplemente se olvida.
    """
    key = f"users/{user_id}/picture"
    await self.storage.put(key, data, content_type)
    user = await self.repository.update(user_id, {"picture": key})
    return await self.sign_picture(user) if user else None

  async def sign_picture(self, user: User) -> User:
    """El bucket de Railway no sirve nada público, así que 'picture' sale firmada.

    Las de proveedor (Google) ya son URL y se dejan tal cual. Caduca en una hora:
    el front la recibe con el usuario y no la guarda.
    """
    if user.picture and not user.picture.startswith("http"):
      user.picture = await self.storage.signed_url(user.picture, "picture")
    return user

  async def delete(self, user_id: ObjectId) -> bool:
    return await self.repository.delete(user_id)
