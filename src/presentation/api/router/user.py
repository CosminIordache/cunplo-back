from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response, status
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.gmail_service import GmailService
from src.application.use_cases.outlook_service import OutlookService
from src.application.use_cases.user_service import EmailAlreadyUsed, UserService
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
  gmail_service: Gmail,
  outlook_service: Outlook,
  current: CurrentUser,
  response: Response,
):
  if id != current.id:
    raise HTTPException(status.HTTP_403_FORBIDDEN, "not your user")
  # primero las integraciones: si el borrado falla, mejor sobra una revocación que un acceso vivo
  await gmail_service.disconnect(id)
  await outlook_service.disconnect(id)
  if not await service.delete(id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
  response.delete_cookie(COOKIE_NAME, path="/")
