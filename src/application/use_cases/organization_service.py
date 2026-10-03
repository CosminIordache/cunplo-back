from typing import Optional

from bson import ObjectId

from src.application.ports.organization_repository import (
  InvitationRepository,
  OrganizationRepository,
)
from src.application.ports.storage import Storage
from src.application.use_cases.resend_service import ResendService
from src.application.use_cases.user_service import UserService
from src.domain.organization import CompanySector, CompanySize, Invitation, Organization
from src.domain.user import Role, User

# lo que un admin de organización puede dar; ADMIN (el del SaaS) nunca sale de aquí
ORG_ROLES = {Role.ORG_ADMIN, Role.USER}
ADMIN_ROLES = {Role.ORG_ADMIN, Role.ADMIN}


class AlreadyInOrganization(Exception):
  pass


class AlreadyMember(Exception):
  pass


class InvalidRole(Exception):
  pass


class LastAdmin(Exception):
  """Una organización con miembros siempre tiene al menos un admin."""


class MemberNotFound(Exception):
  pass


class InvitationNotFound(Exception):
  pass


class OrganizationNotFound(Exception):
  pass


class OrganizationService:
  def __init__(
    self,
    repository: OrganizationRepository,
    invitations: InvitationRepository,
    users: UserService,
    storage: Storage,
    mailer: ResendService,
  ):
    self.repository = repository
    self.invitations = invitations
    self.users = users
    self.storage = storage
    self.mailer = mailer

  async def create(
    self,
    user: User,
    name: str,
    company_size: Optional[CompanySize] = None,
    company_sector: Optional[CompanySector] = None,
  ) -> Organization:
    if user.organization_id:
      raise AlreadyInOrganization
    organization = await self.repository.create(
      Organization(name=name, company_size=company_size, company_sector=company_sector)
    )
    # el creador la administra; el admin del SaaS conserva su rol
    role = Role.ADMIN if user.role == Role.ADMIN else Role.ORG_ADMIN
    await self.users.update(user.id, {"organization_id": organization.id, "role": role})
    return organization

  async def get(self, organization_id: ObjectId) -> Optional[Organization]:
    organization = await self.repository.get(organization_id)
    return await self._sign_picture(organization) if organization else None

  async def update(self, organization_id: ObjectId, changes: dict) -> Optional[Organization]:
    organization = await self.repository.update(organization_id, changes)
    return await self._sign_picture(organization) if organization else None

  async def set_picture(
    self, organization_id: ObjectId, data: bytes, content_type: str
  ) -> Optional[Organization]:
    """Clave fija por organización, como la foto del usuario: sustituir pisa la anterior."""
    key = f"organizations/{organization_id}/picture"
    await self.storage.put(key, data, content_type)
    return await self.update(organization_id, {"picture": key})

  async def _sign_picture(self, organization: Organization) -> Organization:
    if organization.picture:
      organization.picture = await self.storage.signed_url(organization.picture, "picture")
    return organization

  async def members(self, organization_id: ObjectId) -> list[User]:
    members = await self.repository.list_members(organization_id)
    return [await self.users.sign_picture(m) for m in members]

  async def _member(self, organization_id: ObjectId, user_id: ObjectId) -> User:
    member = await self.users.get(user_id)
    if not member or member.organization_id != organization_id:
      raise MemberNotFound
    return member

  async def _check_not_last_admin(self, organization_id: ObjectId, member: User) -> None:
    """Quitar a este admin no puede dejar sin admins a una organización con más gente.
    ponytail: cuenta y luego actualiza; dos admins degradándose a la vez podrían dejarla
    sin ninguno. Pasar a transacción cuando Mongo sea replica set."""
    if member.role not in ADMIN_ROLES:
      return
    members = await self.repository.list_members(organization_id)
    admins = sum(1 for m in members if m.role in ADMIN_ROLES)
    if admins <= 1 and len(members) > 1:
      raise LastAdmin

  async def set_role(
    self, organization_id: ObjectId, user_id: ObjectId, role: Role
  ) -> Optional[User]:
    if role not in ORG_ROLES:
      raise InvalidRole
    member = await self._member(organization_id, user_id)
    if member.role == Role.ADMIN:
      raise InvalidRole  # el rol del SaaS no se toca desde una organización
    if role == Role.USER:
      await self._check_not_last_admin(organization_id, member)
    return await self.users.update(user_id, {"role": role})

  async def remove(
    self, organization_id: ObjectId, user_id: ObjectId, promote_successor: bool = False
  ) -> None:
    """Expulsar o salirse. Si era el último miembro, la organización desaparece.
    Si era el último admin y quedan otros: LastAdmin, o con `promote_successor` (borrar
    la cuenta, donde no hay a quién preguntar) otro miembro pasa a admin."""
    member = await self._member(organization_id, user_id)
    try:
      await self._check_not_last_admin(organization_id, member)
    except LastAdmin:
      if not promote_successor:
        raise
      # ponytail: el de cuenta más antigua (list_members ordena por created_at del
      # usuario), no el que más lleva en la organización; guardar joined_at si importa
      members = await self.repository.list_members(organization_id)
      successor = next(m for m in members if m.id != member.id)
      await self.users.update(successor.id, {"role": Role.ORG_ADMIN})
    await self._leave(member)
    if not await self.repository.list_members(organization_id):
      await self.delete(organization_id)

  async def _leave(self, member: User) -> None:
    """Sale de la organización en una sola escritura. El ORG_ADMIN vuelve a USER;
    el ADMIN del SaaS conserva su rol."""
    role = Role.USER if member.role == Role.ORG_ADMIN else member.role
    await self.users.update(member.id, {"organization_id": None, "role": role})

  async def delete(self, organization_id: ObjectId) -> bool:
    # ponytail: una escritura por miembro; de sobra para equipos de decenas
    for member in await self.repository.list_members(organization_id):
      await self._leave(member)
    await self.invitations.delete_by_org(organization_id)
    await self.storage.delete([f"organizations/{organization_id}/picture"])
    return await self.repository.delete(organization_id)

  async def invite(self, organization_id: ObjectId, inviter: User, email: str) -> Invitation:
    email = email.lower()
    user = await self.users.get_by_email(email)
    if user and user.organization_id == organization_id:
      raise AlreadyMember
    organization = await self.repository.get(organization_id)
    if not organization:
      raise OrganizationNotFound
    invitation = await self.invitations.upsert(
      Invitation(
        organization_id=organization_id,
        organization_name=organization.name,
        email=email,
        invited_by=inviter.id,
      )
    )
    # después de guardarla: si el correo falla, la invitación sigue y se ve en la app.
    # Reinvitar vuelve a mandarlo, que es como se reenvía
    await self.mailer.send_invitation(invitation, inviter.username, inviter.language)
    return invitation

  async def list_invitations(self, organization_id: ObjectId) -> list[Invitation]:
    return await self.invitations.list_by_org(organization_id)

  async def cancel_invitation(self, organization_id: ObjectId, invitation_id: ObjectId) -> bool:
    return await self.invitations.delete(invitation_id, {"organization_id": organization_id})

  async def my_invitations(self, user: User) -> list[Invitation]:
    return await self.invitations.list_by_email(user.email.lower())

  async def accept(self, user: User, invitation_id: ObjectId) -> Organization:
    """El email del usuario lo verificó Google al entrar: solo el invitado puede aceptarla."""
    invitation = await self.invitations.get_for_email(invitation_id, user.email.lower())
    if not invitation:
      raise InvitationNotFound
    if user.organization_id:
      raise AlreadyInOrganization  # tiene que salir de la suya antes
    # antes de tocar al usuario: no puede quedar apuntando a una organización que no existe
    organization = await self.get(invitation.organization_id)
    if not organization:
      raise OrganizationNotFound
    # siempre como miembro; el admin del SaaS conserva su rol
    role = Role.ADMIN if user.role == Role.ADMIN else Role.USER
    await self.users.update(user.id, {"organization_id": invitation.organization_id, "role": role})
    await self.invitations.delete(invitation.id, {})
    return organization

  async def decline(self, user: User, invitation_id: ObjectId) -> bool:
    return await self.invitations.delete(invitation_id, {"email": user.email.lower()})
