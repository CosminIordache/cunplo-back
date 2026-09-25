from typing import Annotated, Optional
from datetime import datetime
from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, model_validator
from pydantic_extra_types.phone_numbers import PhoneNumberValidator

Phone = Annotated[str, PhoneNumberValidator(number_format="E164")]


class ContactCreate(BaseModel):
  email: Optional[EmailStr] = None
  name: Optional[str] = None
  phone: Optional[Phone] = None

  @model_validator(mode="after")
  def _email_or_phone(self):
    # un contacto de WhatsApp no tiene email, pero sin ninguno de los dos no hay a quién buscar
    if not (self.email or self.phone):
      raise ValueError("email or phone is required")
    return self


class ContactUpdate(BaseModel):
  email: Optional[EmailStr] = None
  name: Optional[str] = None
  phone: Optional[Phone] = None


class ContactOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  user_id: Annotated[str, BeforeValidator(str)]
  email: Optional[EmailStr] = None
  name: Optional[str] = None
  phone: Optional[str] = None
  created_at: datetime
  updated_at: datetime
