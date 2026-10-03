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
from src.application.use_cases.task_service import TaskService
from src.infrastructure.external_services.gmail import GmailError
from src.infrastructure.external_services.google_oauth import GMAIL_SCOPE, google
from src.presentation.api.schemas.integration import IntegrationOut
from src.presentation.middleware.auth import CurrentUser
from src.presentation.utils.to_object_id import ObjectIdParam

router = APIRouter(prefix="/integrations", tags=["integrations"])

Service = Annotated[IntegrationService, Depends(Provide[Container.integration_service])]
Gmail = Annotated[GmailService, Depends(Provide[Container.gmail_service])]
Tasks = Annotated[TaskService, Depends(Provide[Container.task_service])]


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


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def disconnect(
  id: ObjectIdParam,
  user: CurrentUser,
  service: Service,
  gmail_service: Gmail,
  tasks: Tasks,
):
  """Por id: el usuario puede tener varias cuentas del mismo provider. Primero se corta el
  acceso; luego se borra lo que vino de esa cuenta (tareas, mensajes y adjuntos)."""
  integration = await service.get(id, user.id)
  if not integration:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "integration not found")
  disconnected = await gmail_service.disconnect(integration)
  await tasks.delete_by_integration(user.id, integration.id)
  return disconnected
