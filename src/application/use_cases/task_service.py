import logfire
from typing import Optional
from bson import ObjectId

from src.domain.task import Task, Status
from src.application.ports.task_repository import TaskRepository
from src.application.use_cases.message_service import MessageService


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
  ) -> Optional[Task]:
    return await self.repository.get_by_thread(user_id, integration_id, thread_id)

  async def get_by_user(
    self, user_id: ObjectId, status: Optional[Status] = None, skip: int = 0, limit: int = 0
  ) -> list[Task]:
    return await self.repository.get_by_user(user_id, status, skip, limit)

  async def update(self, task_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Task]:
    return await self.repository.update(task_id, user_id, changes)

  async def delete(self, task_id: ObjectId, user_id: ObjectId) -> bool:
    """Borrar la tarea se lleva los correos del hilo: sin tarea no hay por qué guardarlos."""
    task = await self.repository.get(task_id, user_id)
    if not task:
      return False

    # la tarea ya sabe de qué buzón sale: solo caen los correos de esa cuenta
    deleted = await self.messages.delete_by_thread(
      user_id, task.integration_id, task.thread_id
    )
    logfire.info(
      "Task {task_id} deleted with {deleted} messages of thread {thread_id}",
      task_id=task_id,
      deleted=deleted,
      thread_id=task.thread_id,
    )
    return await self.repository.delete(task_id, user_id)

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    """Igual que delete pero para todo el usuario: las tareas se llevan sus mensajes."""
    deleted_messages = await self.messages.delete_all_by_user(user_id)
    deleted = await self.repository.delete_all_by_user(user_id)
    logfire.info(
      "Deleted {deleted} tasks and {deleted_messages} messages of user {user_id}",
      deleted=deleted,
      deleted_messages=deleted_messages,
      user_id=user_id,
    )
    return deleted
