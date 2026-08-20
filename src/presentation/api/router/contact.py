from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.contact_service import (
  ContactEmailAlreadyUsed,
  ContactService,
)
from src.domain.contact import Contact
from src.presentation.api.schemas.contact import ContactCreate, ContactUpdate, ContactOut
from src.presentation.middleware.auth import CurrentUser, get_current_user
from src.presentation.utils.to_object_id import ObjectIdParam

# Todo /contacts exige Bearer válido
router = APIRouter(
  prefix="/contacts", tags=["contacts"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[ContactService, Depends(Provide[Container.contact_service])]


@router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
@inject
async def create_contact(payload: ContactCreate, service: Service, current: CurrentUser):
  contact = Contact(user_id=current.id, **payload.model_dump())
  try:
    return await service.create(contact)
  except ContactEmailAlreadyUsed:
    raise HTTPException(status.HTTP_409_CONFLICT, "contact email already used")


@router.get("", response_model=list[ContactOut])
@inject
async def list_contacts(service: Service, current: CurrentUser, search: Optional[str] = None):
  """search filtra por nombre o email (subcadena, case-insensitive)."""
  return await service.get_by_user(current.id, search)


@router.get("/{id}", response_model=ContactOut)
@inject
async def get_contact(id: ObjectIdParam, service: Service, current: CurrentUser):
  contact = await service.get(id, current.id)
  if not contact:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
  return contact


@router.patch("/{id}", response_model=ContactOut)
@inject
async def update_contact(
  id: ObjectIdParam, payload: ContactUpdate, service: Service, current: CurrentUser
):
  changes = payload.model_dump(exclude_unset=True)
  if not changes:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")
  try:
    contact = await service.update(id, current.id, changes)
  except ContactEmailAlreadyUsed:
    raise HTTPException(status.HTTP_409_CONFLICT, "contact email already used")
  if not contact:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
  return contact


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_contact(id: ObjectIdParam, service: Service, current: CurrentUser):
  if not await service.delete(id, current.id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
