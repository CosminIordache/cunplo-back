from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.domain.user import User
from src.application.use_cases.auth_service import (
  AuthService,
  EmailAlreadyUsed,
  InvalidCredentials,
)
from src.presentation.api.schemas.auth import LoginIn, RegisterIn, TokenOut
from src.presentation.api.schemas.user import UserOut
from src.presentation.middleware.auth import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])

Service = Annotated[AuthService, Depends(Provide[Container.auth_service])]


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
@inject
async def register(payload: RegisterIn, service: Service):
  try:
    user, token = await service.register(User(**payload.model_dump()))
  except EmailAlreadyUsed:
    raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
  return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
@inject
async def login(payload: LoginIn, service: Service):
  try:
    user, token = await service.login(payload.email, payload.password)
  except InvalidCredentials:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
  return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
  return user
