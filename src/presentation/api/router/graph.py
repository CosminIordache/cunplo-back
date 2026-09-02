from typing import Annotated
from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.graph_service import GraphService
from src.presentation.api.schemas.graph import GraphOut
from src.presentation.middleware.auth import CurrentUser, get_current_user

router = APIRouter(
  prefix="/graph", tags=["graph"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[GraphService, Depends(Provide[Container.graph_service])]


@router.get("", response_model=GraphOut)
@inject
async def get_graph(service: Service, current: CurrentUser):
  """El grafo de conocimiento del usuario: sus tareas y los contactos de cada una."""
  return await service.build(current.id)
