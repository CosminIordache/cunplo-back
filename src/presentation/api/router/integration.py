import os
from typing import Annotated, Optional

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
from src.application.use_cases.whatsapp_service import GowaError, WhatsAppService
from src.infrastructure.external_services.gmail import GmailError
from src.infrastructure.external_services.google_oauth import GMAIL_SCOPE, google
from src.presentation.api.schemas.integration import (
  IntegrationOut,
  WhatsAppCodeIn,
  WhatsAppCodeOut,
  WhatsAppQrOut,
  WhatsAppStatusOut,
)
from src.presentation.middleware.auth import CurrentUser
from src.presentation.utils.to_object_id import ObjectIdParam

router = APIRouter(prefix="/integrations", tags=["integrations"])

Service = Annotated[IntegrationService, Depends(Provide[Container.integration_service])]
Gmail = Annotated[GmailService, Depends(Provide[Container.gmail_service])]
WhatsApp = Annotated[WhatsAppService, Depends(Provide[Container.whatsapp_service])]


async def disconnect_integration(
  integration,
  gmail_service: GmailService,
  whatsapp_service: WhatsAppService,
) -> bool:
  """Cada provider corta su push a su manera; la fila dice cuál toca."""
  service = {
    Provider.GOOGLE: gmail_service,
    Provider.WHATSAPP: whatsapp_service,
  }[integration.provider]
  return await service.disconnect(integration)


@router.get("", response_model=list[IntegrationOut])
@inject
async def list_integrations(
  user: CurrentUser, service: Service, provider: Optional[Provider] = None
):
  return await service.list_by_user(user.id, provider)


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
async def google_connect_callback(
  request: Request, service: Service, gmail_service: Gmail
):
  user_id = request.session.pop("connect_user_id", None)
  if not user_id:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "connect session expired")
  try:
    token = await google.authorize_access_token(request)
  except OAuthError:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "google auth failed")

  claims = token["userinfo"]
  saved = await service.connect(
    user_id=ObjectId(user_id),
    provider=Provider.GOOGLE,
    account_id=claims["sub"],
    email=claims["email"],
    scopes=token.get("scope", "").split(),
    token=token,
  )

  # El push es opcional: si el topic no está configurado, conectar sigue funcionando
  if os.getenv("PUBSUB_TOPIC"):
    try:
      await gmail_service.start_watch(saved)
    except GmailError:
      logfire.exception("Could not start Gmail watch for {email}", email=saved.email)

  return RedirectResponse(os.getenv("FRONTEND_REDIRECT", "/"))


@router.post("/whatsapp/connect", response_model=WhatsAppQrOut)
@inject
async def whatsapp_connect(user: CurrentUser, whatsapp_service: WhatsApp):
  """QR para vincular un número. Caduca en expires_in segundos: el frontend pide otro y
  consulta /status hasta que logged_in sea true."""
  try:
    device_id, qr, seconds = await whatsapp_service.link_qr(user.id)
  except GowaError:
    logfire.exception("Could not start WhatsApp QR login for user {user_id}", user_id=user.id)
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, "whatsapp service unavailable")
  return WhatsAppQrOut(device_id=device_id, qr=qr, expires_in=seconds)


@router.post("/whatsapp/connect/code", response_model=WhatsAppCodeOut)
@inject
async def whatsapp_connect_code(
  payload: WhatsAppCodeIn, user: CurrentUser, whatsapp_service: WhatsApp
):
  """Vincular sin QR, para cuando Cunplo se usa en el mismo móvil que WhatsApp."""
  try:
    device_id, code = await whatsapp_service.link_code(user.id, payload.phone)
  except GowaError:
    logfire.exception("Could not start WhatsApp code login for user {user_id}", user_id=user.id)
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, "whatsapp service unavailable")
  return WhatsAppCodeOut(device_id=device_id, code=code)


@router.get("/whatsapp/{device_id}/status", response_model=WhatsAppStatusOut)
@inject
async def whatsapp_status(device_id: str, user: CurrentUser, whatsapp_service: WhatsApp):
  """El frontend lo consulta cada pocos segundos tras mostrar el QR o el código. La
  integración se crea aquí, en cuanto GOWA ve el número vinculado."""
  try:
    integration = await whatsapp_service.status(user.id, device_id)
  except GowaError:
    logfire.exception("Could not read WhatsApp status for {device_id}", device_id=device_id)
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, "whatsapp service unavailable")
  return WhatsAppStatusOut(
    device_id=device_id,
    logged_in=integration is not None,
    integration=IntegrationOut.model_validate(integration) if integration else None,
  )


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def disconnect(
  id: ObjectIdParam,
  user: CurrentUser,
  service: Service,
  gmail_service: Gmail,
  whatsapp_service: WhatsApp,
):
  """Por id: el usuario puede tener varias cuentas del mismo provider."""
  integration = await service.get(id, user.id)
  if not integration:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "integration not found")
  return await disconnect_integration(integration, gmail_service, whatsapp_service)
