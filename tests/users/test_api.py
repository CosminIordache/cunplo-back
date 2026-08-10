from bson import ObjectId


def test_create_returns_201_without_password(client, payload):
  response = client.post("/api/v1/users/create", json=payload)
  assert response.status_code == 201, response.text
  body = response.json()
  assert body["username"] == "cosmin"
  assert "password" not in body


def test_create_rejects_invalid_email(client, payload):
  assert client.post("/api/v1/users/create", json={**payload, "email": "nope"}).status_code == 422


def test_create_rejects_missing_field(client, payload):
  del payload["phone"]
  assert client.post("/api/v1/users/create", json=payload).status_code == 422


def test_get_returns_user(client, created_id):
  response = client.get(f"/api/v1/users/{created_id}")
  assert response.status_code == 200
  assert response.json()["id"] == created_id


def test_get_unknown_returns_404(client):
  assert client.get(f"/api/v1/users/{ObjectId()}").status_code == 404


def test_malformed_id_returns_400(client):
  assert client.get("/api/v1/users/no-es-un-objectid").status_code == 400


def test_list_returns_created_users(client, created_id):
  body = client.get("/api/v1/users/list").json()
  assert [u["id"] for u in body] == [created_id]


def test_patch_updates_field(client, created_id):
  response = client.patch(f"/api/v1/users/{created_id}", json={"username": "cosmin2"})
  assert response.status_code == 200, response.text
  assert response.json()["username"] == "cosmin2"


def test_patch_empty_body_returns_400(client, created_id):
  assert client.patch(f"/api/v1/users/{created_id}", json={}).status_code == 400


def test_patch_unknown_returns_404(client):
  assert client.patch(f"/api/v1/users/{ObjectId()}", json={"username": "x"}).status_code == 404


def test_delete_removes_user(client, created_id):
  assert client.delete(f"/api/v1/users/{created_id}").status_code == 204
  assert client.get(f"/api/v1/users/{created_id}").status_code == 404


def test_delete_twice_returns_404(client, created_id):
  client.delete(f"/api/v1/users/{created_id}")
  assert client.delete(f"/api/v1/users/{created_id}").status_code == 404
