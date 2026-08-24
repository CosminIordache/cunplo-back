import logfire
import os
from typing import Annotated

import httpx
from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.domain.integration import Provider
from src.infrastructure.external_services.gmail import GmailError
from src.infrastructure.external_services.google_oauth import GMAIL_SCOPE, google
from src.infrastructure.external_services.microsoft_oauth import (
  CLAIMS_OPTIONS,
  MAIL_SCOPE,
  microsoft,
)
from src.domain.user import User
from src.application.use_cases.auth_service import (
  AuthService,
  EmailAlreadyUsed,
  InvalidCredentials,
)
from src.application.use_cases.gmail_service import GmailService
from src.application.use_cases.integration_service import IntegrationService
from src.application.use_cases.outlook_service import OutlookService
from src.infrastructure.external_services.outlook import OutlookError
from src.infrastructure.utils.security import COOKIE_DOMAIN, COOKIE_NAME, set_session_cookie
from src.presentation.api.schemas.auth import LoginIn, RegisterIn, SessionOut
from src.presentation.api.schemas.user import UserOut
from src.presentation.middleware.auth import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])

Service = Annotated[AuthService, Depends(Provide[Container.auth_service])]
Integrations = Annotated[
  IntegrationService, Depends(Provide[Container.integration_service])
]
Gmail = Annotated[GmailService, Depends(Provide[Container.gmail_service])]
Outlook = Annotated[OutlookService, Depends(Provide[Container.outlook_service])]


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
async def google_callback(
  request: Request, service: Service, integrations: Integrations, gmail_service: Gmail
):
  try:
    token = await google.authorize_access_token(request)
  except OAuthError:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "google auth failed")

  claims = token["userinfo"]
  user, jwt_token = await service.login_oauth(claims)
  saved = await integrations.connect(
    user_id=user.id,
    provider=Provider.GOOGLE,
    account_id=claims["sub"],
    email=claims["email"],
    scopes=token.get("scope", "").split(),
    token=token,
  )

  # El push es opcional: si el topic no está configurado, entrar sigue funcionando
  if os.getenv("PUBSUB_TOPIC"):
    try:
      await gmail_service.start_watch(saved)
    except (GmailError, httpx.HTTPError):
      logfire.exception("Could not start Gmail watch for {email}", email=saved.email)

  redirect = RedirectResponse(os.getenv("FRONTEND_REDIRECT", "/"))
  set_session_cookie(redirect, jwt_token)
  return redirect


@router.get("/microsoft")
async def microsoft_login(request: Request):
  """Entrar y conectar Outlook en un solo consentimiento: offline_access da el refresh_token."""
  return await microsoft.authorize_redirect(
    request,
    str(request.url_for("microsoft_callback")),
    scope=MAIL_SCOPE,
  )


@router.get("/microsoft/callback", name="microsoft_callback")
@inject
async def microsoft_callback(
  request: Request, service: Service, integrations: Integrations, outlook_service: Outlook
):
  try:
    token = await microsoft.authorize_access_token(request, claims_options=CLAIMS_OPTIONS)
  except OAuthError:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "microsoft auth failed")

  claims = token["userinfo"]
  # las cuentas personales no siempre traen 'email': 'preferred_username' es el fallback
  email = claims.get("email") or claims["preferred_username"]
  user, jwt_token = await service.login_oauth({**claims, "email": email})
  saved = await integrations.connect(
    user_id=user.id,
    provider=Provider.MICROSOFT,
    account_id=claims["sub"],
    email=email,
    scopes=token.get("scope", "").split(),
    token=token,
  )

  # El push es opcional: si la URL no está configurada, entrar sigue funcionando
  if os.getenv("GRAPH_NOTIFICATION_URL"):
    try:
      await outlook_service.start_subscription(saved)
    except (OutlookError, httpx.HTTPError):
      logfire.exception("Could not start Graph subscription for {email}", email=saved.email)

  redirect = RedirectResponse(os.getenv("FRONTEND_REDIRECT", "/"))
  set_session_cookie(redirect, jwt_token)
  return redirect
