import os
from typing import Annotated

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.infrastructure.external_services.google_oauth import LOGIN_SCOPE, google
from src.infrastructure.external_services.microsoft_oauth import (
  CLAIMS_OPTIONS,
  LOGIN_SCOPE as MS_LOGIN_SCOPE,
  microsoft,
)
from src.domain.user import AuthProvider, User
from src.application.use_cases.auth_service import (
  AuthService,
)
from src.infrastructure.utils.security import COOKIE_DOMAIN, COOKIE_NAME, set_session_cookie
from src.presentation.api.schemas.auth import LoginIn, RegisterIn, SessionOut
from src.presentation.api.schemas.user import UserOut
from src.presentation.middleware.auth import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])

Service = Annotated[AuthService, Depends(Provide[Container.auth_service])]


# @router.post(
#   "/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED
# )
# @inject
# async def register(payload: RegisterIn, service: Service, response: Response):
#   try:
#     user, token = await service.register(User(**payload.model_dump()))
#   except EmailAlreadyUsed:
#     raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
#   set_session_cookie(response, token)
#   return SessionOut(user=UserOut.model_validate(user))


# @router.post("/login", response_model=SessionOut)
# @inject
# async def login(payload: LoginIn, service: Service, response: Response):
#   try:
#     user, token = await service.login(payload.email, payload.password)
#   except InvalidCredentials:
#     raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
#   set_session_cookie(response, token)
#   return SessionOut(user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
  response.delete_cookie(COOKIE_NAME, path="/", domain=COOKIE_DOMAIN)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
  return user


@router.get("/google")
async def google_login(request: Request):
  """Solo identidad: los buzones se conectan aparte, en /integrations/google/connect."""
  return await google.authorize_redirect(
    request, str(request.url_for("google_callback")), scope=LOGIN_SCOPE
  )


@router.get("/google/callback", name="google_callback")
@inject
async def google_callback(request: Request, service: Service):
  try:
    token = await google.authorize_access_token(request)
  except OAuthError:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "google auth failed")

  _, jwt_token = await service.login_oauth(AuthProvider.GOOGLE, token["userinfo"])

  redirect = RedirectResponse(os.getenv("FRONTEND_REDIRECT", "/"))
  set_session_cookie(redirect, jwt_token)
  return redirect


@router.get("/microsoft")
async def microsoft_login(request: Request):
  """Solo identidad: los buzones se conectan aparte, en /integrations/microsoft/connect."""
  return await microsoft.authorize_redirect(
    request, str(request.url_for("microsoft_callback")), scope=MS_LOGIN_SCOPE
  )


@router.get("/microsoft/callback", name="microsoft_callback")
@inject
async def microsoft_callback(request: Request, service: Service):
  try:
    token = await microsoft.authorize_access_token(request, claims_options=CLAIMS_OPTIONS)
  except OAuthError:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "microsoft auth failed")

  claims = token["userinfo"]
  # las cuentas personales no siempre traen 'email': 'preferred_username' es el fallback
  email = claims.get("email") or claims["preferred_username"]
  _, jwt_token = await service.login_oauth(
    AuthProvider.MICROSOFT, {**claims, "email": email}
  )

  redirect = RedirectResponse(os.getenv("FRONTEND_REDIRECT", "/"))
  set_session_cookie(redirect, jwt_token)
  return redirect
