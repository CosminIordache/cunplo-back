from datetime import datetime, timedelta, UTC

from bson import ObjectId

from src.domain.integration import Integration, Provider
from src.infrastructure.driven import gmail, google_oauth


def test_requires_token(client):
  assert client.get("/api/v1/users/list").status_code == 401


def test_malformed_id_returns_400(client, created_id):
  assert client.patch("/api/v1/users/no-es-un-objectid", json={"username": "x"}).status_code == 400


def test_get_returns_user(client, created_id):
  response = client.get(f"/api/v1/users/{created_id}")
  assert response.status_code == 200
  assert response.json()["id"] == created_id


def test_get_unknown_returns_404(client, created_id):
  assert client.get(f"/api/v1/users/{ObjectId()}").status_code == 404


def test_list_returns_created_users(client, created_id):
  body = client.get("/api/v1/users/list").json()
  assert [u["id"] for u in body] == [created_id]


def test_patch_updates_field(client, created_id):
  response = client.patch(f"/api/v1/users/{created_id}", json={"username": "cosmin2"})
  assert response.status_code == 200, response.text
  assert response.json()["username"] == "cosmin2"


def test_patch_empty_body_returns_400(client, created_id):
  assert client.patch(f"/api/v1/users/{created_id}", json={}).status_code == 400


def test_patch_password_is_hashed_and_login_works(client, created_id, payload, repository):
  assert client.patch(f"/api/v1/users/{created_id}", json={"password": "nueva"}).status_code == 200

  stored = repository.users[[*repository.users][0]]
  assert stored.password != "nueva"
  login = client.post(
    "/api/v1/auth/login", json={"email": payload["email"], "password": "nueva"}
  )
  assert login.status_code == 200


def test_patch_email_to_existing_returns_409(client, created_id, other_id):
  response = client.patch(
    f"/api/v1/users/{created_id}", json={"email": "otro@example.com"}
  )
  assert response.status_code == 409


def test_patch_own_email_unchanged_is_ok(client, created_id, payload):
  response = client.patch(f"/api/v1/users/{created_id}", json={"email": payload["email"]})
  assert response.status_code == 200


def test_patch_unknown_returns_403(client, created_id):
  assert client.patch(f"/api/v1/users/{ObjectId()}", json={"username": "x"}).status_code == 403


def test_patch_other_user_returns_403(client, other_id):
  assert client.patch(f"/api/v1/users/{other_id}", json={"username": "x"}).status_code == 403


def test_delete_other_user_returns_403(client, other_id):
  assert client.delete(f"/api/v1/users/{other_id}").status_code == 403


def test_delete_removes_user(client, created_id, other_id, login_as_other):
  assert client.delete(f"/api/v1/users/{created_id}").status_code == 204
  # la cookie de created_id ya no resuelve: se comprueba con la del otro usuario
  login_as_other()
  body = client.get("/api/v1/users/list")
  assert [u["id"] for u in body.json()] == [other_id]


async def test_delete_user_removes_their_integration(
  client, created_id, integration_repository, monkeypatch
):
  """Borrar la cuenta no puede dejar la integración de Google viva."""
  async def noop(*args):
    return None

  monkeypatch.setattr(gmail, "stop_watch", noop)
  monkeypatch.setattr(google_oauth, "revoke_token", noop)

  user_id = ObjectId(created_id)
  await integration_repository.upsert(
    Integration(
      user_id=user_id, provider=Provider.GOOGLE, account_id="g-1",
      email="ada@example.com", scopes=[], refresh_token="rt", access_token="at",
      expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
  )

  assert client.delete(f"/api/v1/users/{created_id}").status_code == 204
  assert await integration_repository.list_by_user(user_id) == []


def test_deleted_user_token_is_rejected(client, created_id):
  client.delete(f"/api/v1/users/{created_id}")
  assert client.get("/api/v1/users/list").status_code == 401
