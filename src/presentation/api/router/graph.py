from typing import Annotated
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.graph_service import GraphService
from src.presentation.middleware.auth import CurrentUser, get_current_user

router = APIRouter(
  prefix="/graph", tags=["graph"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[GraphService, Depends(Provide[Container.graph_service])]


@router.get("/stream")
@inject
async def stream_graph(service: Service, current: CurrentUser):
  """Igual que GET /graph pero un chunk NDJSON por tarea, para pintar el
  canvas a medida que llegan en vez de esperar las miles de tareas juntas."""
  return StreamingResponse(
    service.stream(current.id), media_type="application/x-ndjson"
  )
