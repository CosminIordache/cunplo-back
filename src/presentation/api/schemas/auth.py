from typing import Optional

from pydantic import BaseModel, EmailStr
from pydantic_extra_types.timezone_name import TimeZoneName
from pydantic_extra_types.language_code import LanguageAlpha2
from src.domain.user import CompanySector, CompanySize
from src.presentation.api.schemas.user import Phone, UserOut


class RegisterIn(BaseModel):
  username: str
  email: EmailStr
  password: str
  phone: Optional[Phone] = None
  timezone: TimeZoneName
  language: LanguageAlpha2
  company_name: Optional[str] = None
  company_size: Optional[CompanySize] = None
  company_sector: Optional[CompanySector] = None


class LoginIn(BaseModel):
  email: EmailStr
  password: str


class SessionOut(BaseModel):
  """El JWT va en la cookie httponly, no en el cuerpo."""
  user: UserOut
