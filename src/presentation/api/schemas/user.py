from typing import Annotated, Optional
from datetime import datetime
from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr
from pydantic_extra_types.timezone_name import TimeZoneName
from pydantic_extra_types.language_code import LanguageAlpha2
from pydantic_extra_types.phone_numbers import PhoneNumberValidator
from src.domain.user import Role

Phone = Annotated[str, PhoneNumberValidator(number_format="E164")]


class UserUpdate(BaseModel):
  username: Optional[str] = None
  email: Optional[EmailStr] = None
  password: Optional[str] = None
  phone: Optional[Phone] = None
  timezone: Optional[TimeZoneName] = None
  language: Optional[LanguageAlpha2] = None
  only_contacts: Optional[bool] = None


class UserOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  username: str
  email: EmailStr
  picture: Optional[str] = None
  phone: Optional[str] = None
  timezone: str
  language: str
  only_contacts: bool = False
  role: str = Role.USER
  created_at: datetime
  updated_at: datetime
