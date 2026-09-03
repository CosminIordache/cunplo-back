from typing import Annotated

from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.usage_service import UsageService
from src.presentation.api.schemas.usage import UsageAllOut, UsageUsersOut
from src.presentation.middleware.auth import AdminUser, get_current_user

router = APIRouter(
  prefix="/usage",
  tags=["usage"],
  dependencies=[Depends(get_current_user)],
)

Service = Annotated[UsageService, Depends(Provide[Container.usage_service])]


@router.get("", response_model=list[UsageUsersOut])
@inject
async def usage_totals(service: Service, admin: AdminUser):
  """El gasto en LLM es interno: solo el admin lo mira.
  ponytail: sin paginar, son tantas filas como usuarios."""
  return await service.totals_by_user()


@router.get("/total", response_model=UsageAllOut)
@inject
async def usage_total_all_users(service: Service, admin: AdminUser):
  """Todo el gasto sumado, para no tener que hacerlo en el cliente."""
  return await service.total_all_users()
