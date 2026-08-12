from pydantic import BaseModel, EmailStr
from pydantic_extra_types.timezone_name import TimeZoneName
from pydantic_extra_types.language_code import LanguageAlpha2

from src.presentation.api.schemas.user import UserOut


class RegisterIn(BaseModel):
  username: str
  email: EmailStr
  password: str
  phone: str
  timezone: TimeZoneName
  language: LanguageAlpha2


class LoginIn(BaseModel):
  email: EmailStr
  password: str


class TokenOut(BaseModel):
  access_token: str
  token_type: str = "bearer"
  user: UserOut
