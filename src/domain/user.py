from dataclasses import dataclass, field
from bson import ObjectId
from datetime import datetime, UTC
from enum import StrEnum
from typing import Optional
from pydantic import EmailStr
from pydantic_extra_types.timezone_name import TimeZoneName
from pydantic_extra_types.language_code import LanguageAlpha2


class AuthProvider(StrEnum):
  GOOGLE = "google"
  MICROSOFT = "microsoft"


class Role(StrEnum):
  USER = "user"
  ADMIN = "admin"

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
class User:
  username: str
  email: EmailStr
  password: Optional[str]
  phone: Optional[str]
  timezone: TimeZoneName
  language: LanguageAlpha2

  company_name: Optional[str] = None
  company_size: Optional[CompanySize] = None
  company_sector: Optional[CompanySector] = None

  # ponytail: URL del proveedor, no la copiamos a storage propio. Microsoft no la da
  # (Graph solo sirve /me/photo/$value binario), así que ahí queda None.
  picture: Optional[str] = None

  # idioma en el que el agente escribe las tareas
  task_language: LanguageAlpha2 = LanguageAlpha2("en")

  # si está activo solo se analizan los correos cuyo remitente ya es contacto del usuario
  only_contacts: bool = False

  # el usuario ya completó el onboarding
  onboarded: bool = False

  role: Role = Role.USER

  # con quién entra: el 'sub' es la identidad, el email puede cambiar o repetirse
  auth_provider: Optional[AuthProvider] = None
  auth_account_id: Optional[str] = None

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))