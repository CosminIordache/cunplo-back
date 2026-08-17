"""Procesado de la notificación de Pub/Sub: del historyId a los mensajes."""
import base64
import json
from datetime import datetime, timedelta, UTC

import pytest
from bson import ObjectId

from src.domain.integration import Provider
from src.domain.message import Message
from src.domain.task import Status
from src.application.use_cases.agent_service import ExtractedTask
from src.application.use_cases.gmail_service import GmailService
from src.infrastructure.driven.redis.functions.process_gmail_notification import (
  process_gmail_notification,
)
from src.infrastructure.external_services import gmail, google_oauth
from tests.integrations.test_integration_service import make_service

EMAIL = "ada@example.com"


@pytest.fixture
def service(integration_repository, monkeypatch):
  integrations = make_service(integration_repository)
  return GmailService(integration_repository, integrations, topic="projects/x/topics/y")


async def _connect(repo, history_id=None, watch_expires_at=None):
  from src.domain.integration import Integration

  integration = Integration(
    user_id=ObjectId(), provider=Provider.GOOGLE, account_id="g-1", email=EMAIL,
    scopes=["gmail.readonly"], refresh_token="rt", access_token="at",
    expires_at=datetime.now(UTC) + timedelta(hours=1), history_id=history_id,
    watch_expires_at=watch_expires_at,
  )
  return await repo.upsert(integration)


async def test_first_notification_only_sets_marker(service, integration_repository, monkeypatch):
  """Sin history_id previo no traemos el pasado: solo empezamos a seguir."""
  await _connect(integration_repository, history_id=None)

  async def boom(*a, **k):
    raise AssertionError("no debería pedir historial")

  monkeypatch.setattr(gmail, "new_message_ids", boom)

  assert await service.process_notification(EMAIL, "500") == []
  stored = await integration_repository.get_by_email(Provider.GOOGLE, EMAIL)
  assert stored.history_id == "500"


async def test_notification_returns_new_messages(service, integration_repository, monkeypatch):
  await _connect(integration_repository, history_id="100")

  async def new_message_ids(token, start):
    assert start == "100"  # arranca donde lo dejamos, no donde dice la notificación
    return ["m1", "m2"], "160"

  async def get_message(token, message_id):
    return {"id": message_id, "labels": ["INBOX"], "subject": f"asunto {message_id}"}

  monkeypatch.setattr(gmail, "new_message_ids", new_message_ids)
  monkeypatch.setattr(gmail, "get_message", get_message)

  messages = await service.process_notification(EMAIL, "160")
  assert [m["id"] for m in messages] == ["m1", "m2"]

  stored = await integration_repository.get_by_email(Provider.GOOGLE, EMAIL)
  assert stored.history_id == "160"  # marcador avanzado


async def test_deleted_message_does_not_break_the_batch(
  service, integration_repository, monkeypatch
):
  """Gmail anuncia un correo y lo borran antes de que lo pidamos: el resto sigue."""
  await _connect(integration_repository, history_id="100")

  async def new_message_ids(token, start):
    return ["m1", "borrado", "m2"], "160"

  async def get_message(token, message_id):
    if message_id == "borrado":
      raise gmail.MessageNotFound(message_id)
    return {"id": message_id, "labels": ["INBOX"], "subject": f"asunto {message_id}"}

  monkeypatch.setattr(gmail, "new_message_ids", new_message_ids)
  monkeypatch.setattr(gmail, "get_message", get_message)

  messages = await service.process_notification(EMAIL, "160")

  assert [m["id"] for m in messages] == ["m1", "m2"]
  stored = await integration_repository.get_by_email(Provider.GOOGLE, EMAIL)
  assert stored.history_id == "160"  # el marcador avanza igual


async def test_history_too_old_resyncs(service, integration_repository, monkeypatch):
  await _connect(integration_repository, history_id="1")

  async def expired(token, start):
    raise gmail.HistoryTooOld

  async def current(token):
    return "999"

  monkeypatch.setattr(gmail, "new_message_ids", expired)
  monkeypatch.setattr(gmail, "current_history_id", current)

  assert await service.process_notification(EMAIL, "999") == []
  stored = await integration_repository.get_by_email(Provider.GOOGLE, EMAIL)
  assert stored.history_id == "999"


def test_gmail_error_wraps_http_status():
  """Un 403 de Gmail no debe subir como HTTPStatusError crudo."""
  import httpx as _httpx

  response = _httpx.Response(403, text="insufficient permissions")
  with pytest.raises(gmail.GmailError, match="403"):
    gmail._check(response)

  ok = _httpx.Response(200, json={"ok": True})
  assert gmail._check(ok) is ok


async def test_unknown_account_is_ignored(service):
  assert await service.process_notification("nadie@example.com", "5") == []


async def test_start_watch_saves_marker_and_expiry(service, integration_repository, monkeypatch):
  integration = await _connect(integration_repository)
  expiration = int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)

  async def watch(token, topic):
    assert topic == "projects/x/topics/y"
    return {"historyId": "42", "expiration": str(expiration)}

  monkeypatch.setattr(gmail, "watch", watch)

  saved = await service.start_watch(integration)
  assert saved.history_id == "42"
  assert saved.watch_expires_at > datetime.now(UTC)


async def _no_history(monkeypatch):
  """La notificación no debe traer mensajes: aquí solo miramos el watch."""
  async def new_message_ids(token, start):
    return [], start

  monkeypatch.setattr(gmail, "new_message_ids", new_message_ids)


async def test_notification_renews_watch_about_to_expire(service, integration_repository, monkeypatch):
  await _connect(
    integration_repository, history_id="100",
    watch_expires_at=datetime.now(UTC) + timedelta(hours=2),
  )
  await _no_history(monkeypatch)
  renewed = int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
  called = {}

  async def watch(token, topic):
    called["topic"] = topic
    return {"historyId": "100", "expiration": str(renewed)}

  monkeypatch.setattr(gmail, "watch", watch)

  await service.process_notification(EMAIL, "100")

  assert called["topic"] == "projects/x/topics/y"
  stored = await integration_repository.get_by_email(Provider.GOOGLE, EMAIL)
  assert stored.watch_expires_at > datetime.now(UTC) + timedelta(days=6)


async def test_notification_does_not_renew_a_fresh_watch(service, integration_repository, monkeypatch):
  await _connect(
    integration_repository, history_id="100",
    watch_expires_at=datetime.now(UTC) + timedelta(days=5),
  )
  await _no_history(monkeypatch)

  async def boom(*a, **k):
    raise AssertionError("no debería renovar un watch aún vigente")

  monkeypatch.setattr(gmail, "watch", boom)

  await service.process_notification(EMAIL, "100")


async def test_failed_renewal_does_not_break_the_notification(service, integration_repository, monkeypatch):
  """El watch vigente todavía sirve: un fallo al renovar no puede tumbar el push."""
  await _connect(
    integration_repository, history_id="100",
    watch_expires_at=datetime.now(UTC) + timedelta(hours=2),
  )

  async def boom(token, topic):
    raise gmail.GmailError("Gmail API 500")

  async def new_message_ids(token, start):
    return ["m1"], "160"

  async def get_message(token, message_id):
    return {"id": message_id, "labels": ["INBOX"]}

  monkeypatch.setattr(gmail, "watch", boom)
  monkeypatch.setattr(gmail, "new_message_ids", new_message_ids)
  monkeypatch.setattr(gmail, "get_message", get_message)

  messages = await service.process_notification(EMAIL, "160")
  assert [m["id"] for m in messages] == ["m1"]  # el correo se procesa igual


async def test_disconnect_stops_watch_and_revokes(service, integration_repository, monkeypatch):
  """Sin parar el watch Google seguiría publicando en Pub/Sub tras el borrado."""
  integration = await _connect(integration_repository, history_id="100")
  called = {}

  async def stop_watch(token):
    called["stopped"] = token

  async def revoke(token):
    called["revoked"] = token

  monkeypatch.setattr(gmail, "stop_watch", stop_watch)
  monkeypatch.setattr(google_oauth, "revoke_token", revoke)

  assert await service.disconnect(integration.user_id) is True
  assert called == {"stopped": "at", "revoked": "rt"}
  assert await integration_repository.get_by_email(Provider.GOOGLE, EMAIL) is None

  # y una notificación que llegue tarde ya no encuentra cuenta
  assert await service.process_notification(EMAIL, "200") == []


async def test_disconnect_of_other_user_does_nothing(service, integration_repository, monkeypatch):
  await _connect(integration_repository)

  async def boom(*a, **k):
    raise AssertionError("no debería tocar Google")

  monkeypatch.setattr(gmail, "stop_watch", boom)

  assert await service.disconnect(ObjectId()) is False
  assert await integration_repository.get_by_email(Provider.GOOGLE, EMAIL) is not None


async def test_disconnect_revokes_even_if_stop_watch_fails(service, integration_repository, monkeypatch):
  """Si parar el watch falla, revocar es lo único que corta el acceso: hay que intentarlo."""
  integration = await _connect(integration_repository)
  called = {}

  async def boom(token):
    raise gmail.GmailError("Gmail API 401")

  async def revoke(token):
    called["revoked"] = token

  monkeypatch.setattr(gmail, "stop_watch", boom)
  monkeypatch.setattr(google_oauth, "revoke_token", revoke)

  assert await service.disconnect(integration.user_id) is True
  assert called == {"revoked": "rt"}  # revocado pese al fallo del stop
  assert await integration_repository.get_by_email(Provider.GOOGLE, EMAIL) is None


def test_webhook_decodes_pubsub_envelope(client, queue, monkeypatch):
  """El endpoint driving: Pub/Sub manda el payload en base64 y nosotros encolamos."""
  monkeypatch.setenv("PUBSUB_TOKEN", "secreto")

  data = base64.b64encode(
    json.dumps({"emailAddress": EMAIL, "historyId": 777}).encode()
  ).decode()
  response = client.post(
    "/api/v1/webhooks/gmail?token=secreto", json={"message": {"data": data}}
  )

  assert response.status_code == 204
  # el historyId viaja como string: arq serializa a JSON y Gmail lo manda como número
  assert queue.jobs == [("process_gmail_notification", EMAIL, "777")]


async def test_worker_job_reaches_the_service():
  """El otro lado de la cola: lo que el worker desencola acaba en el servicio."""
  seen = {}

  class FakeGmailService:
    async def process_notification(self, email, history_id):
      seen.update(email=email, history_id=history_id)
      return []

  await process_gmail_notification({"gmail_service": FakeGmailService()}, EMAIL, "777")

  assert seen == {"email": EMAIL, "history_id": "777"}


async def test_worker_job_saves_the_messages(integration_repository):
  """El correo entero acaba en db, con el dueño que la cola no trae."""
  integration = await _connect(integration_repository, history_id="100")
  saved, tasks = [], []

  class FakeGmailService:
    async def process_notification(self, email, history_id):
      return [{
        "id": "m1", "thread_id": "t1", "subject": "hola", "sender": "ada@example.com",
        "to": "bob@example.com", "cc": "", "body": "texto", "internal_date": 1700000000000,
      }]

  class FakeMessageService:
    async def upsert(self, message):
      saved.append(message)
      return message

    async def list_by_thread_id_user_id(self, user_id, thread_id):
      return []

  class FakeAgentService:
    async def run_tasks(self, owner_email, thread_messages, new_message):
      return ExtractedTask(title="responder a ada", status=Status.TODO, contacts=[])

  class FakeTaskService:
    async def upsert(self, task):
      tasks.append(task)
      return task

  ctx = {
    "gmail_service": FakeGmailService(),
    "integration_service": make_service(integration_repository),
    "message_service": FakeMessageService(),
    "agent_service": FakeAgentService(),
    "task_service": FakeTaskService(),
    "contact_service": _FakeContactService(),
  }
  await process_gmail_notification(ctx, EMAIL, "160")

  assert len(saved) == 1
  message = saved[0]
  assert message.provider_id == "m1"
  assert message.subject == "hola"
  assert message.body == "texto"
  assert message.user_id == integration.user_id  # el dueño sale de la integración
  assert message.integration_id == integration.id
  assert message.cc is None  # sin copia: ausente, no cadena vacía
  assert tasks[0].thread_id == "t1"
  assert tasks[0].title == "responder a ada"


class _FakeContactService:
  def __init__(self):
    self.created = []

  async def get_by_email(self, user_id, email):
    return next((c for c in self.created if c.email == email), None)

  async def create(self, contact):
    self.created.append(contact)
    return contact


async def test_worker_job_never_saves_the_owner_as_contact(integration_repository):
  """Su propia cuenta no acaba como contacto, la devuelva el agente o no."""
  from src.application.use_cases.agent_service import ExtractedContact

  await _connect(integration_repository, history_id="100")
  contacts, tasks = _FakeContactService(), []

  class FakeGmailService:
    async def process_notification(self, email, history_id):
      return [{
        "id": "m1", "thread_id": "t1", "subject": "hola", "sender": "laura@acme.com",
        "to": EMAIL, "cc": "", "body": "texto", "internal_date": 1700000000000,
      }]

  class FakeMessageService:
    async def upsert(self, message):
      return message

    async def list_by_thread_id_user_id(self, user_id, thread_id):
      return []

  class FakeAgentService:
    async def run_tasks(self, owner_email, thread_messages, new_message):
      return ExtractedTask(
        title="responder", status=Status.TODO,
        contacts=[
          ExtractedContact(email=EMAIL.upper()),  # él mismo, y en mayúsculas
          ExtractedContact(email="laura@acme.com", name="Laura"),
        ],
      )

  class FakeTaskService:
    async def upsert(self, task):
      tasks.append(task)
      return task

  ctx = {
    "gmail_service": FakeGmailService(),
    "integration_service": make_service(integration_repository),
    "message_service": FakeMessageService(),
    "agent_service": FakeAgentService(),
    "contact_service": contacts,
    "task_service": FakeTaskService(),
  }
  await process_gmail_notification(ctx, EMAIL, "160")

  assert [c.email for c in contacts.created] == ["laura@acme.com"]
  assert len(tasks[0].contact_ids) == 1


_MESSAGE = Message(
  user_id=ObjectId(), integration_id=ObjectId(), provider_id="m1", thread_id="t1",
  sender="ada@example.com", to="bob@example.com", subject="presupuesto",
  body="¿me pasas precio?", internal_date=1600000000000,
)


async def test_worker_job_skips_mail_without_task(integration_repository):
  """Sin tarea no se guarda, aunque el hilo ya tenga otros correos: la publicidad
  que el cliente cuela en un hilo que sí es tarea tampoco entra."""
  await _connect(integration_repository, history_id="100")
  saved = []

  class FakeGmailService:
    async def process_notification(self, email, history_id):
      return [{
        "id": "m2", "thread_id": "t1", "subject": "publicidad", "sender": "ada@example.com",
        "to": "bob@example.com", "cc": "", "body": "mira mi oferta", "internal_date": 1700000000000,
      }]

  class FakeMessageService:
    async def upsert(self, message):
      saved.append(message)
      return message

    async def list_by_thread_id_user_id(self, user_id, thread_id):
      return [_MESSAGE]  # el hilo ya es una tarea

  class FakeAgentService:
    async def run_tasks(self, owner_email, thread_messages, new_message):
      return None

  ctx = {
    "gmail_service": FakeGmailService(),
    "integration_service": make_service(integration_repository),
    "message_service": FakeMessageService(),
    "agent_service": FakeAgentService(),
  }
  await process_gmail_notification(ctx, EMAIL, "160")

  assert saved == []


def test_webhook_rejects_wrong_token(client, monkeypatch):
  monkeypatch.setenv("PUBSUB_TOKEN", "secreto")
  response = client.post("/api/v1/webhooks/gmail?token=malo", json={"message": {}})
  assert response.status_code == 403


async def test_drafts_are_not_stored(monkeypatch):
  """El borrador se indexa mientras se escribe y desaparece al enviar: no debe guardarse."""
  from src.infrastructure.external_services import gmail
  from src.application.use_cases.gmail_service import GmailService

  raw = {
    "id": "draft1",
    "labelIds": ["DRAFT"],
    "internalDate": "1",
    "payload": {"headers": [], "mimeType": "text/plain", "body": {}},
  }
  assert gmail._to_message(raw)["labels"] == ["DRAFT"]
