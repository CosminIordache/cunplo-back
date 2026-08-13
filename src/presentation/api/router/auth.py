import os
from typing import Annotated

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.domain.integration import Provider
from src.infrastructure.driven.google_oauth import GMAIL_SCOPE, google
from src.domain.user import User
from src.application.use_cases.auth_service import (
  AuthService,
  EmailAlreadyUsed,
  InvalidCredentials,
)
from src.application.use_cases.integration_service import IntegrationService
from src.presentation.api.schemas.auth import LoginIn, RegisterIn, TokenOut
from src.presentation.api.schemas.user import UserOut
from src.presentation.middleware.auth import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])

Service = Annotated[AuthService, Depends(Provide[Container.auth_service])]
Integrations = Annotated[
  IntegrationService, Depends(Provide[Container.integration_service])
]


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
@inject
async def register(payload: RegisterIn, service: Service):
  try:
    user, token = await service.register(User(**payload.model_dump()))
  except EmailAlreadyUsed:
    raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
  return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
@inject
async def login(payload: LoginIn, service: Service):
  try:
    user, token = await service.login(payload.email, payload.password)
  except InvalidCredentials:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
  return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
  return user


@router.get("/google")
async def google_login(request: Request):
  """Entrar y conectar Gmail en un solo consentimiento: offline da el refresh_token."""
  return await google.authorize_redirect(
    request,
    str(request.url_for("google_callback")),
    scope=GMAIL_SCOPE,
    access_type="offline",
    prompt="consent",
  )


@router.get("/google/callback", name="google_callback")
@inject
async def google_callback(request: Request, service: Service, integrations: Integrations):
  try:
    token = await google.authorize_access_token(request)
  except OAuthError:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "google auth failed")

  claims = token["userinfo"]
  user, jwt_token = await service.login_google(claims)
  await integrations.connect(
    user_id=user.id,
    provider=Provider.GOOGLE,
    account_id=claims["sub"],
    email=claims["email"],
    scopes=token.get("scope", "").split(),
    token=token,
  )
  return RedirectResponse(f"{os.getenv('FRONTEND_REDIRECT', '/')}#token={jwt_token}")
