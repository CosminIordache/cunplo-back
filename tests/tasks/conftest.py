from datetime import datetime, UTC
import pytest
from bson import ObjectId

from src.main import container
from src.domain.message import Message
from src.domain.task import Task, Status


class FakeTaskRepository:
  """ponytail: repo en memoria; el filtro por user_id se replica tal cual lo hace Mongo."""

  def __init__(self):
    self.tasks: dict[ObjectId, Task] = {}

  async def upsert(self, task: Task) -> Task:
    # un hilo, una tarea: si ya existe conserva su id
    existing = await self.get_by_thread(task.user_id, task.thread_id)
    if existing:
      task.id = existing.id
      # como el $addToSet de Mongo: los contactos se suman, no se sustituyen
      task.contact_ids = existing.contact_ids + [
        c for c in task.contact_ids if c not in existing.contact_ids
      ]
    self.tasks[task.id] = task
    return task

  async def get(self, task_id, user_id):
    task = self.tasks.get(task_id)
    return task if task and task.user_id == user_id else None

  async def get_by_thread(self, user_id, thread_id):
    return next(
      (t for t in self.tasks.values()
       if t.user_id == user_id and t.thread_id == thread_id),
      None,
    )

  async def get_by_user(self, user_id, status=None) -> list[Task]:
    return [
      t for t in self.tasks.values()
      if t.user_id == user_id and (not status or t.status == status)
    ]

  async def update(self, task_id, user_id, changes):
    task = self.tasks.get(task_id)
    if not task or task.user_id != user_id:
      return None
    for key, value in changes.items():
      setattr(task, key, value)
    task.updated_at = datetime.now(UTC)
    return task

  async def delete(self, task_id, user_id) -> bool:
    task = self.tasks.get(task_id)
    if not task or task.user_id != user_id:
      return False
    del self.tasks[task_id]
    return True


class FakeMessageRepository:
  def __init__(self):
    self.messages: dict[ObjectId, Message] = {}

  async def upsert(self, message: Message) -> Message:
    self.messages[message.id] = message
    return message

  async def list_by_thread_id_user_id(self, user_id, thread_id) -> list[Message]:
    return sorted(
      (m for m in self.messages.values()
       if m.user_id == user_id and m.thread_id == thread_id),
      key=lambda m: m.internal_date,
    )

  async def delete(self, message_id, user_id) -> bool:
    message = self.messages.get(message_id)
    if not message or message.user_id != user_id:
      return False
    del self.messages[message_id]
    return True

  async def delete_by_thread(self, user_id, thread_id) -> int:
    ids = [
      m.id for m in self.messages.values()
      if m.user_id == user_id and m.thread_id == thread_id
    ]
    for message_id in ids:
      del self.messages[message_id]
    return len(ids)


PAYLOAD = {
  "username": "cosmin",
  "email": "cosmin@example.com",
  "password": "secret",
  "timezone": "Europe/Madrid",
  "language": "es",
}


@pytest.fixture
def payload() -> dict:
  return dict(PAYLOAD)


@pytest.fixture
def repository() -> FakeTaskRepository:
  return FakeTaskRepository()


@pytest.fixture
def message_repository() -> FakeMessageRepository:
  return FakeMessageRepository()


@pytest.fixture
def overrides(repository, message_repository) -> dict:
  from tests.users.conftest import FakeUserRepository

  return {
    container.task_repository: repository,
    container.message_repository: message_repository,
    container.user_repository: FakeUserRepository(),
  }


@pytest.fixture
def user_id(client, payload) -> ObjectId:
  """Registra al usuario (deja su cookie) y devuelve su id."""
  body = client.post("/api/v1/auth/register", json=payload).json()
  return ObjectId(body["user"]["id"])


@pytest.fixture
def task(repository, message_repository, user_id) -> Task:
  """Una tarea con dos correos en su hilo, como la deja el worker."""
  task = Task(user_id=user_id, thread_id="t1", title="enviar presupuesto", status=Status.TODO)
  repository.tasks[task.id] = task
  for index in (1, 2):
    message = Message(
      user_id=user_id, integration_id=ObjectId(), provider_id=f"m{index}", thread_id="t1",
      sender="ada@example.com", to="bob@example.com", subject="presupuesto",
      body="texto", internal_date=1700000000000 + index,
    )
    message_repository.messages[message.id] = message
  return task
