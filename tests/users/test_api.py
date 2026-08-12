from bson import ObjectId


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


def test_delete_removes_user(client, created_id, other_id, other_token):
  assert client.delete(f"/api/v1/users/{created_id}").status_code == 204
  # el token de created_id ya no resuelve: se comprueba con el del otro usuario
  body = client.get("/api/v1/users/list", headers={"Authorization": f"Bearer {other_token}"})
  assert [u["id"] for u in body.json()] == [other_id]


def test_deleted_user_token_is_rejected(client, created_id):
  client.delete(f"/api/v1/users/{created_id}")
  assert client.get("/api/v1/users/list").status_code == 401
