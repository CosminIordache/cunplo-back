from typing import Annotated, Optional
from datetime import datetime
from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr
from pydantic_extra_types.phone_numbers import PhoneNumberValidator

Phone = Annotated[str, PhoneNumberValidator(number_format="E164")]


class ContactCreate(BaseModel):
  email: EmailStr
  name: Optional[str] = None
  phone: Optional[Phone] = None


class ContactUpdate(BaseModel):
  email: Optional[EmailStr] = None
  name: Optional[str] = None
  phone: Optional[Phone] = None


class ContactOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  user_id: Annotated[str, BeforeValidator(str)]
  email: EmailStr
  name: Optional[str] = None
  phone: Optional[str] = None
  created_at: datetime
  updated_at: datetime
