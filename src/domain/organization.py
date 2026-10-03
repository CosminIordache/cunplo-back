from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from enum import StrEnum
from typing import Optional

from bson import ObjectId

INVITATION_DAYS = 7


class CompanySize(StrEnum):
  S1_10 = "1-10"
  S11_50 = "11-50"
  S51_200 = "51-200"
  S201_PLUS = "201+"


class CompanySector(StrEnum):
  REAL_ESTATE = "real_estate"
  AGENCY_MARKETING = "agency_marketing"
  CONSULTING = "consulting"
  LEGAL = "legal"
  SOFTWARE_SAAS = "software_saas"
  RETAIL = "retail"
  HEALTH = "health"
  OTHER = "other"


@dataclass
class Organization:
  """Los miembros no viven aquí: cada User lleva su organization_id y su role."""

  name: str
  picture: Optional[str] = None  # clave en el bucket, sale firmada como la del usuario
  company_size: Optional[CompanySize] = None
  company_sector: Optional[CompanySector] = None

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Invitation:
  """Invitación por email. Aceptarla o rechazarla la borra: no hay estados que guardar.
  Sin rol: siempre se entra como USER, y un admin lo sube después si hace falta."""

  organization_id: ObjectId
  organization_name: str  # ponytail: copia al invitar; si la org se renombra, la invitación sigue con el viejo
  email: str  # en minúsculas
  invited_by: ObjectId

  expires_at: datetime = field(
    default_factory=lambda: datetime.now(UTC) + timedelta(days=INVITATION_DAYS)
  )
  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
