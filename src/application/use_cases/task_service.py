import logfire
from typing import Optional
from bson import ObjectId

from src.domain.task import Task, Status
from src.application.ports.task_repository import TaskRepository
from src.application.use_cases.message_service import MessageService


def existing_task_id(task_id: Optional[str], thread_tasks: list[Task]) -> Optional[ObjectId]:
  """El id que devuelve el agente solo vale si es de una tarea del hilo; si no, la tarea es nueva."""
  return next((t.id for t in thread_tasks if str(t.id) == task_id), None)


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

  async def get_by_user(
    self, user_id: ObjectId, status: Optional[Status] = None, skip: int = 0, limit: int = 0
  ) -> list[Task]:
    return await self.repository.get_by_user(user_id, status, skip, limit)

  async def update(self, task_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Task]:
    return await self.repository.update(task_id, user_id, changes)

  async def delete(self, task_id: ObjectId, user_id: ObjectId) -> bool:
    """La última tarea del hilo se lleva sus correos: sin tarea no hay por qué guardarlos.
    Si quedan otras, los correos se quedan: son su contexto."""
    task = await self.repository.get(task_id, user_id)
    if not task:
      return False

    siblings = await self.repository.get_by_thread(user_id, task.integration_id, task.thread_id)
    if any(t.id != task.id for t in siblings):
      logfire.info(
        "Task {task_id} deleted, thread {thread_id} keeps its messages for {remaining} tasks",
        task_id=task_id,
        thread_id=task.thread_id,
        remaining=len(siblings) - 1,
      )
      return await self.repository.delete(task_id, user_id)

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
