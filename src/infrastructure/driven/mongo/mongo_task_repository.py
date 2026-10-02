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
    """Por id: el job decide si el correo actualiza una tarea o crea otra. Usuario y
    cuenta van en el filtro: un id ajeno no toca nada, inserta. Los hilos se acumulan:
    una tarea actualizada desde otro hilo pasa a estar también en él."""
    task.updated_at = datetime.now(UTC)
    document = _to_document(task)
    document.pop("_id")
    document.pop("created_at")
    # priority va en el $set a propósito: el agente no la pone, así que cada actualización
    # la deja en null. La prioridad es la urgencia del dueño para actuar; cuando el correo
    # cambia la tarea (p.ej. ya enviaste y ahora esperas al cliente) esa urgencia ya no vale.
    # Los contactos se acumulan en vez de sustituirse: el agente solo mira el correo
    # nuevo, y los de correos anteriores del hilo ya no vuelven a salir.
    contact_ids = document.pop("contact_ids")
    thread_ids = document.pop("thread_ids")
    doc = await self.collection.find_one_and_update(
      {
        "_id": task.id,
        "user_id": task.user_id,
        "integration_id": task.integration_id,
      },
      {
        "$set": document,
        "$addToSet": {"contact_ids": {"$each": contact_ids}, "thread_ids": {"$each": thread_ids}},
        "$setOnInsert": {"created_at": task.created_at},
      },
      upsert=True,
      return_document=True,
    )
    return _to_task(doc)

  async def get(self, task_id: ObjectId, user_id: ObjectId) -> Optional[Task]:
    doc = await self.collection.find_one({"_id": task_id, "user_id": user_id})
    return _to_task(doc) if doc else None

  async def get_by_thread(
    self, user_id: ObjectId, integration_id: ObjectId, thread_id: str
  ) -> list[Task]:
    cursor = self.collection.find(
      {"user_id": user_id, "integration_id": integration_id, "thread_ids": thread_id}
    ).sort("created_at", 1)
    return [_to_task(d) async for d in cursor]

  async def get_by_threads(
    self, user_id: ObjectId, integration_id: ObjectId, thread_ids: list[str]
  ) -> list[Task]:
    cursor = self.collection.find(
      {"user_id": user_id, "integration_id": integration_id, "thread_ids": {"$in": thread_ids}}
    ).sort("created_at", 1)
    return [_to_task(d) async for d in cursor]

  async def get_by_user(
    self, user_id: ObjectId, status: Optional[Status] = None, skip: int = 0, limit: int = 0
  ) -> list[Task]:
    # user_id en el filtro: nadie lee las tareas de otro
    query = {"user_id": user_id}
    if status:
      query["status"] = status
    cursor = self.collection.find(query)
    # Las sin due_at salen primero (null ordena antes en Mongo).
    # Si molesta, ordena en el service o mete un $sort con $ifNull en un pipeline.
    cursor = cursor.sort([("due_at", 1), ("created_at", -1)]).skip(skip)
    if limit:
      cursor = cursor.limit(limit)
    return [_to_task(d) async for d in cursor]

  async def update(self, task_id: ObjectId, user_id: ObjectId, changes: dict) -> Optional[Task]:
    changes["updated_at"] = datetime.now(UTC)
    doc = await self.collection.find_one_and_update(
      {"_id": task_id, "user_id": user_id}, {"$set": changes}, return_document=True
    )
    return _to_task(doc) if doc else None

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    result = await self.collection.delete_many({"user_id": user_id})
    return result.deleted_count

  async def delete(self, task_id: ObjectId, user_id: ObjectId) -> bool:
    result = await self.collection.delete_one({"_id": task_id, "user_id": user_id})
    return result.deleted_count == 1
