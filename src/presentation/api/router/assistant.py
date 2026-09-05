from typing import Annotated
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide
from pydantic import BaseModel, Field

from src.container import Container
from src.application.use_cases.assistant_agent_service import AssistantService
from src.presentation.middleware.auth import ProUser

# solo plan pro (active o canceled): el guard hace de puerta, no hace falta otro de cookie
router = APIRouter(prefix="/assistant", tags=["assistant"])

Service = Annotated[AssistantService, Depends(Provide[Container.assistant_service])]


class Question(BaseModel):
  question: str = Field(min_length=1)


class Answer(BaseModel):
  answer: str
  task_ids: list[str]
  contact_ids: list[str]
  message_ids: list[str]

@router.post("/ask/stream")
@inject
async def ask_stream(payload: Question, current: ProUser, service: Service):
  """Igual que /ask pero NDJSON: {"delta": "..."} por trozo de texto y una última
  línea con task_ids / contact_ids / message_ids. El cliente pinta el texto según llega."""
  return StreamingResponse(
    service.ask_stream(current.id, current.email, current.language, payload.question),
    media_type="application/x-ndjson",
  )
