from typing import Annotated, Literal, Optional
from datetime import datetime

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

from src.domain.organization import CompanySector, CompanySize
from src.domain.user import Role

# lo que se puede dar desde una organización: nunca "admin", que es el del SaaS
OrgRole = Literal[Role.ORG_ADMIN, Role.USER]


class OrganizationCreate(BaseModel):
  name: str = Field(min_length=1)
  company_size: Optional[CompanySize] = None
  company_sector: Optional[CompanySector] = None


class OrganizationUpdate(BaseModel):
  name: Optional[str] = Field(default=None, min_length=1)
  # null borra el dato: son opcionales
  company_size: Optional[CompanySize] = None
  company_sector: Optional[CompanySector] = None


class OrganizationOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  name: str
  picture: Optional[str] = None  # URL firmada, caduca en una hora
  company_size: Optional[CompanySize] = None
  company_sector: Optional[CompanySector] = None
  created_at: datetime
  updated_at: datetime


class MemberRoleUpdate(BaseModel):
  role: OrgRole


class InvitationCreate(BaseModel):
  email: EmailStr  # sin rol: siempre se entra como miembro


class InvitationOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]
  organization_id: Annotated[str, BeforeValidator(str)]
  organization_name: str
  # solo en las recibidas: URL firmada de la imagen de la organización
  organization_picture: Optional[str] = None
  email: str
  invited_by: Annotated[str, BeforeValidator(str)]
  expires_at: datetime
  created_at: datetime
