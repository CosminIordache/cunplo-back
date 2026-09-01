"""Guard de cookie: valida el JWT de la sesión y resuelve el usuario de la petición."""
from typing import Annotated, Optional

from bson import ObjectId
from bson.errors import InvalidId
from dependency_injector.wiring import inject, Provide
from fastapi import Cookie, Depends, HTTPException, status
from joserfc.errors import JoseError

from src.container import Container
from src.domain.user import Role, User
from src.application.use_cases.auth_service import AuthService
from src.application.use_cases.subscription_service import SubscriptionService
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


async def get_admin_user(user: CurrentUser) -> User:
  if user.role != Role.ADMIN:
    raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
  return user


AdminUser = Annotated[User, Depends(get_admin_user)]


@inject
async def require_active_subscription(
  user: CurrentUser,
  subscriptions: Annotated[
    SubscriptionService, Depends(Provide[Container.subscription_service])
  ],
) -> User:
  """Puerta de pago: 402 si el trial caducó y no hay plan vivo.
  Los admins pasan siempre, para poder mirar la casa por dentro."""
  if user.role == Role.ADMIN:
    return user
  subscription = await subscriptions.get_by_user(user.id)
  if not subscription or not subscription.is_active:
    raise HTTPException(
      status.HTTP_402_PAYMENT_REQUIRED, "subscription expired"
    )
  return user


SubscribedUser = Annotated[User, Depends(require_active_subscription)]
