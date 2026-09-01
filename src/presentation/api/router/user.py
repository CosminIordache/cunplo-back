from typing import Annotated
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.contact_service import ContactService
from src.application.use_cases.gmail_service import GmailService
from src.application.use_cases.integration_service import IntegrationService
from src.application.use_cases.outlook_service import OutlookService
from src.application.use_cases.task_service import TaskService
from src.application.use_cases.user_service import EmailAlreadyUsed, UserService
from src.presentation.api.router.integration import disconnect_integration
from src.infrastructure.utils.security import COOKIE_NAME
from src.presentation.api.schemas.user import UserUpdate, UserOut
from src.presentation.middleware.auth import CurrentUser, get_current_user
from src.presentation.utils.to_object_id import ObjectIdParam

# Todo /users exige Bearer válido
router = APIRouter(
  prefix="/users", tags=["users"], dependencies=[Depends(get_current_user)]
)


Service = Annotated[UserService, Depends(Provide[Container.user_service])]
Gmail = Annotated[GmailService, Depends(Provide[Container.gmail_service])]
Outlook = Annotated[OutlookService, Depends(Provide[Container.outlook_service])]
Integrations = Annotated[
  IntegrationService, Depends(Provide[Container.integration_service])
]
Contacts = Annotated[ContactService, Depends(Provide[Container.contact_service])]
Tasks = Annotated[TaskService, Depends(Provide[Container.task_service])]

@router.get("/list", response_model=list[UserOut])
@inject
async def list_users(service: Service):
  return await service.list()


@router.get("/{id}", response_model=UserOut)
@inject
async def get_user(id: ObjectIdParam, service: Service):
  user = await service.get(id)
  if not user:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
  return user


# tipos que el navegador muestra sin descargar; el resto no es una foto de perfil
PICTURE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PICTURE_BYTES = 5 * 1024 * 1024


@router.put("/{id}/picture", response_model=UserOut)
@inject
async def set_user_picture(
  id: ObjectIdParam,
  service: Service,
  current: CurrentUser,
  file: UploadFile = File(...),
):
  if id != current.id:
    raise HTTPException(status.HTTP_403_FORBIDDEN, "not your user")
  if file.content_type not in PICTURE_TYPES:
    raise HTTPException(
      status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "picture must be jpeg, png or webp"
    )
  data = await file.read(MAX_PICTURE_BYTES + 1)
  if len(data) > MAX_PICTURE_BYTES:
    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "picture too large")
  user = await service.set_picture(id, data, file.content_type)
  if not user:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
  return user


@router.patch("/{id}", response_model=UserOut)
@inject
async def update_user(
  id: ObjectIdParam, payload: UserUpdate, service: Service, current: CurrentUser
):
  if id != current.id:
    raise HTTPException(status.HTTP_403_FORBIDDEN, "not your user")
  changes = payload.model_dump(exclude_unset=True)
  if not changes:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")
  try:
    user = await service.update(id, changes)
  except EmailAlreadyUsed:
    raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
  if not user:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
  return user


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_user(
  id: ObjectIdParam,
  service: Service,
  integrations: Integrations,
  contacts: Contacts,
  tasks: Tasks,
  gmail_service: Gmail,
  outlook_service: Outlook,
  current: CurrentUser,
  response: Response,
):
  if id != current.id:
    raise HTTPException(status.HTTP_403_FORBIDDEN, "not your user")
  # primero las integraciones: si el borrado falla, mejor sobra una revocación que un acceso vivo
  for integration in await integrations.list_by_user(id):
    await disconnect_integration(integration, gmail_service, outlook_service)
  # ponytail: cascada secuencial sin transacción; si falla a medias quedan huérfanos
  # por user_id — pasar a una transacción cuando Mongo sea replica set
  await tasks.delete_all_by_user(id)  # se lleva también los mensajes
  await contacts.delete_all_by_user(id)
  if not await service.delete(id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
  response.delete_cookie(COOKIE_NAME, path="/")
