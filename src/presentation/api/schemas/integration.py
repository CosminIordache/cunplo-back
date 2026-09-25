from typing import Annotated, Optional
from datetime import datetime

from pydantic import BaseModel, BeforeValidator, ConfigDict
from pydantic_extra_types.phone_numbers import PhoneNumberValidator


class IntegrationOut(BaseModel):
  """Nunca expone tokens: solo qué cuenta está conectada y con qué permisos."""

  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]
  provider: str
  account_id: str
  email: str
  scopes: list[str]
  created_at: datetime
  updated_at: datetime


class WhatsAppQrOut(BaseModel):
  device_id: str  # aún no hay integración: nace cuando se vincula el número
  qr: str  # data URL del PNG: se pinta tal cual en un <img>
  expires_in: int  # segundos; pasado ese tiempo hay que pedir otro


class WhatsAppCodeIn(BaseModel):
  phone: Annotated[str, PhoneNumberValidator(number_format="E164")]


class WhatsAppCodeOut(BaseModel):
  device_id: str
  code: str  # se escribe en WhatsApp > Dispositivos vinculados > Vincular con número


class WhatsAppStatusOut(BaseModel):
  device_id: str
  logged_in: bool
  integration: Optional[IntegrationOut] = None  # la recién creada, en cuanto logged_in
