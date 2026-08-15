from typing import Optional
from dataclasses import asdict
from datetime import datetime, UTC
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.domain.task import Task, Status


def _to_document(task: Task) -> dict:
  document = asdict(task)
  document["_id"] = document.pop("id")
  return document


def _to_task(document: dict) -> Task:
  document = dict(document)
  document["id"] = document.pop("_id")
  return Task(**document)


class MongoTaskRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["tasks"]

  async def upsert(self, task: Task) -> Task:
    """Un hilo, una tarea: el segundo correo del hilo actualiza en vez de duplicar."""
    task.updated_at = datetime.now(UTC)
    document = _to_document(task)
    document.pop("_id")
    document.pop("created_at")
    await self.collection.update_one(
      {"user_id": task.user_id, "thread_id": task.thread_id},
      {"$set": document, "$setOnInsert": {"_id": task.id, "created_at": task.created_at}},
      upsert=True,
    )
    return task

  async def get_by_thread(self, user_id: ObjectId, thread_id: str) -> Optional[Task]:
    doc = await self.collection.find_one({"user_id": user_id, "thread_id": thread_id})
    return _to_task(doc) if doc else None

  async def get_by_user(self, user_id: ObjectId, status: Optional[Status] = None) -> list[Task]:
    # user_id en el filtro: nadie lee las tareas de otro
    query = {"user_id": user_id}
    if status:
      query["status"] = status
    cursor = self.collection.find(query)
    # Las sin due_at salen primero (null ordena antes en Mongo).
    # Si molesta, ordena en el service o mete un $sort con $ifNull en un pipeline.
    return [_to_task(d) async for d in cursor.sort([("due_at", 1), ("created_at", -1)])]

  async def update(self, task_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Task]:
    changes["updated_at"] = datetime.now(UTC)
    doc = await self.collection.find_one_and_update(
      {"_id": task_id, "user_id": user_id}, {"$set": changes}, return_document=True
    )
    return _to_task(doc) if doc else None

  async def delete(self, task_id: ObjectId, user_id: ObjectId) -> bool:
    result = await self.collection.delete_one({"_id": task_id, "user_id": user_id})
    return result.deleted_count == 1
