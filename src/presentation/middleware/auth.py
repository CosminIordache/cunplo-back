"""Guard de cookie: valida el JWT de la sesión y resuelve el usuario de la petición."""
from typing import Annotated, Optional

from bson import ObjectId
from bson.errors import InvalidId
from dependency_injector.wiring import inject, Provide
from fastapi import Cookie, Depends, HTTPException, status
from joserfc.errors import JoseError

from src.container import Container
from src.domain.user import User
from src.application.use_cases.auth_service import AuthService
from src.infrastructure.utils.security import COOKIE_NAME, decode_token


@inject
async def get_current_user(
  service: Annotated[AuthService, Depends(Provide[Container.auth_service])],
  session: Annotated[Optional[str], Cookie(alias=COOKIE_NAME)] = None,
) -> User:
  if not session:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
  try:
    claims = decode_token(session)
    user_id = ObjectId(claims["sub"])
  except (JoseError, KeyError, InvalidId):
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
  user = await service.current(user_id)
  if not user:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
  return user


CurrentUser = Annotated[User, Depends(get_current_user)]
