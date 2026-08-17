from bson import ObjectId

from src.domain.task import Task, Status


def test_list_tasks(client, task):
  tasks = client.get("/api/v1/tasks").json()
  assert [t["id"] for t in tasks] == [str(task.id)]
  assert tasks[0]["title"] == "enviar presupuesto"
  assert tasks[0]["thread_id"] == "t1"


async def test_upsert_accumulates_contacts(repository, user_id):
  """El segundo correo del hilo no borra los contactos del primero."""
  ada, bob = ObjectId(), ObjectId()
  await repository.upsert(
    Task(user_id=user_id, thread_id="t5", title="presupuesto",
         status=Status.TODO, contact_ids=[ada])
  )
  # el agente solo ve el correo nuevo: ada ya no vuelve a salir
  await repository.upsert(
    Task(user_id=user_id, thread_id="t5", title="presupuesto",
         status=Status.TODO, contact_ids=[bob])
  )

  stored = await repository.get_by_thread(user_id, "t5")
  assert stored.contact_ids == [ada, bob]

  # y sin contactos nuevos, los de antes siguen ahí
  await repository.upsert(
    Task(user_id=user_id, thread_id="t5", title="presupuesto",
         status=Status.TODO, contact_ids=[])
  )
  assert (await repository.get_by_thread(user_id, "t5")).contact_ids == [ada, bob]


def test_list_filters_by_status(client, repository, task, user_id):
  done = Task(user_id=user_id, thread_id="t2", title="ya está", status=Status.DONE)
  repository.tasks[done.id] = done

  tasks = client.get("/api/v1/tasks?task_status=done").json()
  assert [t["id"] for t in tasks] == [str(done.id)]


def test_list_hides_other_users_tasks(client, repository, task):
  ajena = Task(
    user_id=ObjectId(), thread_id="t9", title="de otro", status=Status.TODO
  )
  repository.tasks[ajena.id] = ajena

  tasks = client.get("/api/v1/tasks").json()
  assert [t["id"] for t in tasks] == [str(task.id)]


def test_update_task(client, task):
  response = client.patch(
    f"/api/v1/tasks/{task.id}",
    json={"title": "llamar al cliente", "status": "done", "due_at": "2026-09-01T00:00:00Z"},
  )

  assert response.status_code == 200
  body = response.json()
  assert body["title"] == "llamar al cliente"
  assert body["status"] == "done"
  assert body["due_at"].startswith("2026-09-01")


def test_update_only_one_field_keeps_the_rest(client, task):
  body = client.patch(f"/api/v1/tasks/{task.id}", json={"status": "waiting_response"}).json()

  assert body["status"] == "waiting_response"
  assert body["title"] == "enviar presupuesto"  # lo no enviado no se toca


def test_update_can_clear_due_at(client, repository, task):
  from datetime import datetime, UTC

  task.due_at = datetime(2026, 1, 1, tzinfo=UTC)

  body = client.patch(f"/api/v1/tasks/{task.id}", json={"due_at": None}).json()

  assert body["due_at"] is None


def test_update_without_fields_is_400(client, task):
  assert client.patch(f"/api/v1/tasks/{task.id}", json={}).status_code == 400


def test_update_bad_status_is_422(client, task):
  assert client.patch(f"/api/v1/tasks/{task.id}", json={"status": "nope"}).status_code == 422


def test_update_other_users_task_is_404(client, repository, user_id):
  ajena = Task(user_id=ObjectId(), thread_id="t9", title="de otro", status=Status.TODO)
  repository.tasks[ajena.id] = ajena

  response = client.patch(f"/api/v1/tasks/{ajena.id}", json={"title": "mía ahora"})

  assert response.status_code == 404
  assert ajena.title == "de otro"


def test_delete_task_also_deletes_its_thread(client, task, message_repository, user_id):
  assert len(message_repository.messages) == 2

  response = client.delete(f"/api/v1/tasks/{task.id}")

  assert response.status_code == 204
  assert client.get("/api/v1/tasks").json() == []
  # sin tarea no hay por qué guardar los correos del hilo
  assert message_repository.messages == {}


def test_delete_keeps_messages_of_other_threads(
  client, task, message_repository, user_id
):
  from src.domain.message import Message

  otro = Message(
    user_id=user_id, integration_id=ObjectId(), provider_id="m9", thread_id="t2",
    sender="ada@example.com", to="bob@example.com", subject="otro hilo",
    body="texto", internal_date=1700000000000,
  )
  message_repository.messages[otro.id] = otro

  client.delete(f"/api/v1/tasks/{task.id}")

  assert list(message_repository.messages) == [otro.id]


def test_delete_unknown_task_is_404(client, user_id):
  response = client.delete(f"/api/v1/tasks/{ObjectId()}")
  assert response.status_code == 404


def test_delete_other_users_task_is_404(client, repository, message_repository, user_id):
  """El dueño va en el filtro: la tarea ajena ni se ve ni se borra."""
  ajena = Task(user_id=ObjectId(), thread_id="t9", title="de otro", status=Status.TODO)
  repository.tasks[ajena.id] = ajena

  response = client.delete(f"/api/v1/tasks/{ajena.id}")

  assert response.status_code == 404
  assert ajena.id in repository.tasks


def test_malformed_id_is_400(client, user_id):
  assert client.delete("/api/v1/tasks/nope").status_code == 400


def test_tasks_require_auth(client):
  assert client.get("/api/v1/tasks").status_code == 401


def test_list_thread_messages(client, task, message_repository):
  messages = client.get("/api/v1/messages/thread/t1").json()

  assert [m["provider_id"] for m in messages] == ["m1", "m2"]  # por internal_date
  assert messages[0]["subject"] == "presupuesto"


def test_list_thread_messages_of_other_user_is_empty(client, message_repository, user_id):
  from src.domain.message import Message

  ajeno = Message(
    user_id=ObjectId(), integration_id=ObjectId(), provider_id="m9", thread_id="t9",
    sender="ada@example.com", to="bob@example.com", subject="de otro",
    body="texto", internal_date=1700000000000,
  )
  message_repository.messages[ajeno.id] = ajeno

  assert client.get("/api/v1/messages/thread/t9").json() == []


def test_unknown_thread_is_empty(client, user_id):
  assert client.get("/api/v1/messages/thread/nope").json() == []


def test_messages_require_auth(client):
  assert client.get("/api/v1/messages/thread/t1").status_code == 401
