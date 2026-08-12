from typing import Annotated, Optional
from datetime import datetime
from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr
from pydantic_extra_types.timezone_name import TimeZoneName
from pydantic_extra_types.language_code import LanguageAlpha2


class UserUpdate(BaseModel):
  username: Optional[str] = None
  email: Optional[EmailStr] = None
  password: Optional[str] = None
  phone: Optional[str] = None
  timezone: Optional[TimeZoneName] = None
  language: Optional[LanguageAlpha2] = None


class UserOut(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: Annotated[str, BeforeValidator(str)]  # ObjectId -> str
  username: str
  email: EmailStr
  phone: str
  timezone: str
  language: str
  created_at: datetime
  updated_at: datetime
