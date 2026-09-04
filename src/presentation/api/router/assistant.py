from typing import Annotated
from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide
from pydantic import BaseModel, Field

from src.container import Container
from src.application.use_cases.assistant_agent_service import AssistantService
from src.presentation.middleware.auth import CurrentUser, get_current_user

router = APIRouter(
  prefix="/assistant", tags=["assistant"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[AssistantService, Depends(Provide[Container.assistant_service])]


class Question(BaseModel):
  question: str = Field(min_length=1)


class Answer(BaseModel):
  answer: str
  task_ids: list[str]
  contact_ids: list[str]
  message_ids: list[str]


@router.post("/ask", response_model=Answer)
@inject
async def ask(payload: Question, current: CurrentUser, service: Service):
  return await service.ask(current.id, current.email, current.language, payload.question)
