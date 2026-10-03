from typing import Annotated
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.organization_service import (
  AlreadyInOrganization,
  AlreadyMember,
  InvalidRole,
  InvitationNotFound,
  LastAdmin,
  MemberNotFound,
  OrganizationNotFound,
  OrganizationService,
)
from src.domain.user import Role
from src.presentation.api.router.user import MAX_PICTURE_BYTES, PICTURE_TYPES
from src.presentation.api.schemas.organization import (
  InvitationCreate,
  InvitationOut,
  MemberRoleUpdate,
  OrganizationCreate,
  OrganizationOut,
  OrganizationUpdate,
)
from src.presentation.api.schemas.user import UserOut
from src.presentation.middleware.auth import AdminOrgId, CurrentUser, OrgId, get_current_user
from src.presentation.utils.to_object_id import ObjectIdParam

# /me es siempre la organización del usuario de la cookie: no hay forma de pedir otra
router = APIRouter(
  prefix="/organizations", tags=["organizations"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[OrganizationService, Depends(Provide[Container.organization_service])]

LAST_ADMIN = "an organization with members needs at least one admin"


@router.post("", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED)
@inject
async def create_organization(payload: OrganizationCreate, service: Service, current: CurrentUser):
  try:
    return await service.create(
      current, payload.name, payload.company_size, payload.company_sector
    )
  except AlreadyInOrganization:
    raise HTTPException(status.HTTP_409_CONFLICT, "already in an organization")


@router.get("/me", response_model=OrganizationOut)
@inject
async def get_my_organization(service: Service, organization_id: OrgId):
  organization = await service.get(organization_id)
  if not organization:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
  return organization


@router.patch("/me", response_model=OrganizationOut)
@inject
async def update_my_organization(payload: OrganizationUpdate, service: Service, organization_id: AdminOrgId):
  changes = payload.model_dump(exclude_unset=True)
  if not changes:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")
  organization = await service.update(organization_id, changes)
  if not organization:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
  return organization


@router.put("/me/picture", response_model=OrganizationOut)
@inject
async def set_organization_picture(
  service: Service, organization_id: AdminOrgId, file: UploadFile = File(...)
):
  if file.content_type not in PICTURE_TYPES:
    raise HTTPException(
      status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "picture must be jpeg, png or webp"
    )
  data = await file.read(MAX_PICTURE_BYTES + 1)
  if len(data) > MAX_PICTURE_BYTES:
    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "picture too large")
  organization = await service.set_picture(organization_id, data, file.content_type)
  if not organization:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
  return organization


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_my_organization(service: Service, organization_id: AdminOrgId):
  """Saca a todos los miembros; sus datos no se tocan."""
  if not await service.delete(organization_id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")


@router.get("/me/members", response_model=list[UserOut])
@inject
async def list_members(service: Service, organization_id: OrgId):
  return await service.members(organization_id)


@router.patch("/me/members/{user_id}", response_model=UserOut)
@inject
async def set_member_role(
  user_id: ObjectIdParam, payload: MemberRoleUpdate, service: Service, organization_id: AdminOrgId
):
  try:
    member = await service.set_role(organization_id, user_id, Role(payload.role))
  except MemberNotFound:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
  except InvalidRole:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "role cannot be changed")
  except LastAdmin:
    raise HTTPException(status.HTTP_409_CONFLICT, LAST_ADMIN)
  if not member:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
  return member


@router.delete("/me/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def remove_member(
  user_id: ObjectIdParam, service: Service, organization_id: OrgId, current: CurrentUser
):
  """Un admin expulsa a cualquiera; cualquier miembro puede salirse a sí mismo."""
  if user_id != current.id and current.role not in (Role.ORG_ADMIN, Role.ADMIN):
    raise HTTPException(status.HTTP_403_FORBIDDEN, "only organization admins")
  try:
    await service.remove(organization_id, user_id)
  except MemberNotFound:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
  except LastAdmin:
    raise HTTPException(status.HTTP_409_CONFLICT, LAST_ADMIN)


@router.post(
  "/me/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED
)
@inject
async def invite(
  payload: InvitationCreate, service: Service, organization_id: AdminOrgId, current: CurrentUser
):
  try:
    return await service.invite(
      organization_id, current, payload.email
    )
  except AlreadyMember:
    raise HTTPException(status.HTTP_409_CONFLICT, "already a member")
  except OrganizationNotFound:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")


@router.get("/me/invitations", response_model=list[InvitationOut])
@inject
async def list_invitations(service: Service, organization_id: AdminOrgId):
  return await service.list_invitations(organization_id)


@router.delete("/me/invitations/{id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def cancel_invitation(id: ObjectIdParam, service: Service, organization_id: AdminOrgId):
  if not await service.cancel_invitation(organization_id, id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "invitation not found")


@router.get("/invitations", response_model=list[InvitationOut])
@inject
async def my_invitations(service: Service, current: CurrentUser):
  """Las que ha recibido el usuario de la cookie, por su email."""
  return await service.my_invitations(current)


@router.post("/invitations/{id}/accept", response_model=OrganizationOut)
@inject
async def accept_invitation(id: ObjectIdParam, service: Service, current: CurrentUser):
  try:
    return await service.accept(current, id)
  except (InvitationNotFound, OrganizationNotFound):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "invitation not found")
  except AlreadyInOrganization:
    raise HTTPException(status.HTTP_409_CONFLICT, "already in an organization")


@router.delete("/invitations/{id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def decline_invitation(id: ObjectIdParam, service: Service, current: CurrentUser):
  if not await service.decline(current, id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "invitation not found")
