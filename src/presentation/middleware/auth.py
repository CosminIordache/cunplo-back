"""Guard de Bearer: valida el JWT y resuelve el usuario de la petición."""
from typing import Annotated

from bson import ObjectId
from bson.errors import InvalidId
from dependency_injector.wiring import inject, Provide
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from joserfc.errors import JoseError

from src.container import Container
from src.domain.user import User
from src.application.use_cases.auth_service import AuthService
from src.infrastructure.driven.security import decode_token

bearer = HTTPBearer()


@inject
async def get_current_user(
  credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
  service: Annotated[AuthService, Depends(Provide[Container.auth_service])],
) -> User:
  try:
    claims = decode_token(credentials.credentials)
    user_id = ObjectId(claims["sub"])
  except (JoseError, KeyError, InvalidId):
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
  user = await service.current(user_id)
  if not user:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
  return user


CurrentUser = Annotated[User, Depends(get_current_user)]
