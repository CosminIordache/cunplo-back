import os
from typing import Annotated

import logfire

from authlib.integrations.base_client import OAuthError
from bson import ObjectId
from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from src.container import Container
from src.domain.integration import Provider
from src.application.use_cases.gmail_service import GmailService
from src.application.use_cases.integration_service import IntegrationService
from src.application.use_cases.outlook_service import OutlookService
from src.infrastructure.external_services.outlook import OutlookError
from src.infrastructure.external_services.google_oauth import GMAIL_SCOPE, google
from src.infrastructure.external_services.microsoft_oauth import (
  CLAIMS_OPTIONS,
  MAIL_SCOPE,
  microsoft,
)
from src.presentation.api.schemas.integration import IntegrationOut
from src.presentation.middleware.auth import CurrentUser

router = APIRouter(prefix="/integrations", tags=["integrations"])

Service = Annotated[IntegrationService, Depends(Provide[Container.integration_service])]
Gmail = Annotated[GmailService, Depends(Provide[Container.gmail_service])]
Outlook = Annotated[OutlookService, Depends(Provide[Container.outlook_service])]


@router.get("", response_model=list[IntegrationOut])
@inject
async def list_integrations(user: CurrentUser, service: Service):
  return await service.list_by_user(user.id)


@router.get("/google/connect")
async def google_connect(request: Request, user: CurrentUser):
  """Pide gmail.readonly con consentimiento offline: es lo único que da refresh_token."""
  # Google no reenvía el Bearer al volver, así que el usuario viaja en la sesión firmada
  request.session["connect_user_id"] = str(user.id)
  return await google.authorize_redirect(
    request,
    str(request.url_for("google_connect_callback")),
    scope=GMAIL_SCOPE,
    access_type="offline",
    prompt="consent",
  )


@router.get("/google/callback", name="google_connect_callback")
@inject
async def google_connect_callback(request: Request, service: Service):
  user_id = request.session.pop("connect_user_id", None)
  if not user_id:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "connect session expired")
  try:
    token = await google.authorize_access_token(request)
  except OAuthError:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "google auth failed")

  claims = token["userinfo"]
  await service.connect(
    user_id=ObjectId(user_id),
    provider=Provider.GOOGLE,
    account_id=claims["sub"],
    email=claims["email"],
    scopes=token.get("scope", "").split(),
    token=token,
  )
  return RedirectResponse(os.getenv("FRONTEND_REDIRECT", "/"))


@router.delete("/google", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def disconnect(user: CurrentUser, gmail_service: Gmail):
  """Sin id: el usuario solo tiene una integración por provider."""
  if not await gmail_service.disconnect(user.id, Provider.GOOGLE):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "integration not found")


@router.get("/microsoft/connect")
async def microsoft_connect(request: Request, user: CurrentUser):
  """Conectar Outlook con sesión ya iniciada (por Google o por Microsoft)."""
  # Microsoft tampoco reenvía el Bearer al volver: el usuario viaja en la sesión firmada
  request.session["connect_user_id"] = str(user.id)
  return await microsoft.authorize_redirect(
    request,
    str(request.url_for("microsoft_connect_callback")),
    scope=MAIL_SCOPE,
  )


@router.get("/microsoft/callback", name="microsoft_connect_callback")
@inject
async def microsoft_connect_callback(
  request: Request, service: Service, outlook_service: Outlook
):
  user_id = request.session.pop("connect_user_id", None)
  if not user_id:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "connect session expired")
  try:
    token = await microsoft.authorize_access_token(request, claims_options=CLAIMS_OPTIONS)
  except OAuthError:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "microsoft auth failed")

  claims = token["userinfo"]
  saved = await service.connect(
    user_id=ObjectId(user_id),
    provider=Provider.MICROSOFT,
    account_id=claims["sub"],
    email=claims.get("email") or claims["preferred_username"],
    scopes=token.get("scope", "").split(),
    token=token,
  )

  # El push es opcional: si la URL no está configurada, conectar sigue funcionando
  if os.getenv("GRAPH_NOTIFICATION_URL"):
    try:
      await outlook_service.start_subscription(saved)
    except OutlookError:
      logfire.exception("Could not start Graph subscription for {email}", email=saved.email)

  return RedirectResponse(os.getenv("FRONTEND_REDIRECT", "/"))


@router.delete("/microsoft", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def microsoft_disconnect(user: CurrentUser, outlook_service: Outlook):
  if not await outlook_service.disconnect(user.id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "integration not found")
