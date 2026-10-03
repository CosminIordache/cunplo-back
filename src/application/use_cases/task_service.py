import logfire
from typing import Optional
from bson import ObjectId

from src.domain.task import Task, Status
from src.application.ports.task_repository import TaskRepository
from src.application.use_cases.message_service import MessageService


def existing_task(task_id: Optional[str], tasks: list[Task]) -> Optional[Task]:
  """El id que devuelve el agente solo vale si es de una de las tareas que se le dieron;
  si no, la tarea es nueva."""
  return next((t for t in tasks if str(t.id) == task_id), None)


def existing_task_id(task_id: Optional[str], thread_tasks: list[Task]) -> Optional[ObjectId]:
  task = existing_task(task_id, thread_tasks)
  return task.id if task else None


class TaskService:
  def __init__(self, repository: TaskRepository, messages: MessageService):
    self.repository = repository
    self.messages = messages

  async def upsert(self, task: Task) -> Task:
    return await self.repository.upsert(task)

  async def get(self, task_id: ObjectId, user_id: ObjectId) -> Optional[Task]:
    return await self.repository.get(task_id, user_id)

  async def get_by_thread(
    self, user_id: ObjectId, integration_id: ObjectId, thread_id: str
  ) -> list[Task]:
    return await self.repository.get_by_thread(user_id, integration_id, thread_id)

  async def get_by_threads(
    self, user_id: ObjectId, integration_id: ObjectId, thread_ids: list[str]
  ) -> list[Task]:
    return await self.repository.get_by_threads(user_id, integration_id, thread_ids)

  async def get_by_user(
    self, user_id: ObjectId, status: Optional[Status] = None, skip: int = 0, limit: int = 0
  ) -> list[Task]:
    return await self.repository.get_by_user(user_id, status, skip, limit)

  async def update(self, task_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Task]:
    return await self.repository.update(task_id, user_id, changes)

  async def delete(self, task_id: ObjectId, user_id: ObjectId) -> bool:
    """Solo la tarea: los correos se quedan, son el contexto de lo que se habla con esas
    personas aunque ya no tengan tarea."""
    return await self.repository.delete(task_id, user_id)

  async def delete_by_integration(self, user_id: ObjectId, integration_id: ObjectId) -> int:
    """Al desconectar una cuenta: sus tareas y sus mensajes (con adjuntos) se van con ella."""
    deleted_messages = await self.messages.delete_by_integration(user_id, integration_id)
    deleted = await self.repository.delete_by_integration(user_id, integration_id)
    logfire.info(
      "Deleted {deleted} tasks and {deleted_messages} messages of integration {integration_id}",
      deleted=deleted,
      deleted_messages=deleted_messages,
      integration_id=integration_id,
    )
    return deleted

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    """Al borrar la cuenta: todas las tareas y todos los mensajes del usuario."""
    deleted_messages = await self.messages.delete_all_by_user(user_id)
    deleted = await self.repository.delete_all_by_user(user_id)
    logfire.info(
      "Deleted {deleted} tasks and {deleted_messages} messages of user {user_id}",
      deleted=deleted,
      deleted_messages=deleted_messages,
      user_id=user_id,
    )
    return deleted
