from typing import Annotated

from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.usage_service import UsageService
from src.presentation.api.schemas.usage import UsageTotalOut
from src.presentation.middleware.auth import AdminUser, get_current_user

router = APIRouter(
  prefix="/usage",
  tags=["usage"],
  dependencies=[Depends(get_current_user)],
)

Service = Annotated[UsageService, Depends(Provide[Container.usage_service])]


@router.get("", response_model=list[UsageTotalOut])
@inject
async def usage_totals(service: Service, admin: AdminUser):
  """El gasto en LLM es interno: solo el admin lo mira.
  ponytail: sin paginar, son tantas filas como usuarios."""
  return await service.totals_by_user()
