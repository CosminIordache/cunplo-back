from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from dependency_injector.wiring import inject, Provide

from src.container import Container
from src.application.use_cases.task_service import TaskService
from src.domain.task import Status as TaskStatus
from src.presentation.api.schemas.task import TaskOut, TaskUpdate
from src.presentation.middleware.auth import CurrentUser, get_current_user
from src.presentation.utils.to_object_id import ObjectIdParam

router = APIRouter(
  prefix="/tasks", tags=["tasks"], dependencies=[Depends(get_current_user)]
)

Service = Annotated[TaskService, Depends(Provide[Container.task_service])]


@router.get("", response_model=list[TaskOut])
@inject
async def list_tasks(
  service: Service, current: CurrentUser, task_status: Optional[TaskStatus] = None
):
  return await service.get_by_user(current.id, task_status)


@router.patch("/{id}", response_model=TaskOut)
@inject
async def update_task(
  id: ObjectIdParam, payload: TaskUpdate, service: Service, current: CurrentUser
):
  # exclude_unset: due_at=None es quitar la fecha, no viene lo mismo que no mandarlo
  changes = payload.model_dump(exclude_unset=True)
  if not changes:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")
  task = await service.update(id, current.id, changes)
  if not task:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
  return task


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_task(id: ObjectIdParam, service: Service, current: CurrentUser):
  # el servicio se lleva también los correos del hilo
  if not await service.delete(id, current.id):
    raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
