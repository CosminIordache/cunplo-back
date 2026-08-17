from typing import Annotated, List
from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.message_service import MessageService
from src.presentation.api.schemas.message import MessageOut
from src.presentation.middleware.auth import CurrentUser, get_current_user

# Todo /messages exige Bearer válido
router = APIRouter(
  prefix="/messages", tags=["messages"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[MessageService, Depends(Provide[Container.message_service])]


@router.get("/thread/{thread_id}", response_model=list[MessageOut])
@inject
async def list_thread_messages(thread_id: str, service: Service, current: CurrentUser):
  # el hilo sale ordenado por internal_date; vacío si no es del usuario
  return await service.list_by_thread_id_user_id(current.id, thread_id)
