from typing import Optional
from bson import ObjectId

from src.domain.task import Task, Status
from src.application.ports.task_repository import TaskRepository


class TaskService:
  def __init__(self, repository: TaskRepository):
    self.repository = repository

  async def upsert(self, task: Task) -> Task:
    return await self.repository.upsert(task)

  async def get_by_thread(self, user_id: ObjectId, thread_id: str) -> Optional[Task]:
    return await self.repository.get_by_thread(user_id, thread_id)

  async def get_by_user(self, user_id: ObjectId, status: Optional[Status] = None) -> list[Task]:
    return await self.repository.get_by_user(user_id, status)

  async def update(self, task_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Task]:
    return await self.repository.update(task_id, user_id, changes)

  async def delete(self, task_id: ObjectId, user_id: ObjectId) -> bool:
    return await self.repository.delete(task_id, user_id)
