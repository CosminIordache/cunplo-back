from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.domain.user import User
from src.application.use_cases.user_service import UserService
from src.presentation.api.schemas.user import UserCreate, UserUpdate, UserOut
from src.presentation.utils.to_object_id import ObjectIdParam

router = APIRouter(prefix="/users", tags=["users"])


Service = Annotated[UserService, Depends(Provide[Container.user_service])]

@router.post("/create", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@inject
async def create_user(payload: UserCreate, service: Service):
  user = await service.create(User(**payload.model_dump()))
  return user


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
async def update_user(id: ObjectIdParam, payload: UserUpdate, service: Service):
  changes = payload.model_dump(exclude_unset=True)
  if not changes:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")
  user = await service.update(id, changes)
  if not user:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
  return user


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_user(id: ObjectIdParam, service: Service):
  if not await service.delete(id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
