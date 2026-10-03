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


class Role(StrEnum):
  USER = "user"
  ORG_ADMIN = "org_admin"  # administra su organización
  ADMIN = "admin"  # administrador del SaaS

@dataclass
class User:
  username: str
  email: EmailStr
  password: Optional[str]
  phone: Optional[str]
  timezone: TimeZoneName
  language: LanguageAlpha2

  # ponytail: URL del proveedor, no la copiamos a storage propio.
  picture: Optional[str] = None

  # idioma en el que el agente escribe las tareas
  task_language: LanguageAlpha2 = LanguageAlpha2("en")

  # solo se analizan los mensajes de quien ya es contacto del usuario
  only_contacts: bool = False

  # el usuario ya completó el onboarding
  onboarded: bool = False

  role: Role = Role.USER

  # como mucho una organización; el rol dentro de ella es `role`
  organization_id: Optional[ObjectId] = None

  # con quién entra: el 'sub' es la identidad, el email puede cambiar o repetirse
  auth_provider: Optional[AuthProvider] = None
  auth_account_id: Optional[str] = None

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
  updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))