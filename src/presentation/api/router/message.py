from typing import Annotated, List
from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.message_service import MessageService
from src.presentation.api.schemas.message import MessageOut
from src.presentation.middleware.auth import CurrentUser, get_current_user
from src.presentation.utils.to_object_id import ObjectIdParam

# Todo /messages exige Bearer válido
router = APIRouter(
  prefix="/messages", tags=["messages"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[MessageService, Depends(Provide[Container.message_service])]


@router.get("/thread/{integration_id}/{thread_id}", response_model=list[MessageOut])
@inject
async def list_thread_messages(
  integration_id: ObjectIdParam,
  thread_id: str,
  service: Service,
  current: CurrentUser,
):
  # la cuenta va en la ruta: el mismo thread_id puede existir en dos buzones
  # el hilo sale ordenado por internal_date; vacío si no es del usuario
  return await service.list_by_thread_id_user_id(current.id, integration_id, thread_id)
