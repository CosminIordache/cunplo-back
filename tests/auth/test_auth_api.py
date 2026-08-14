from src.infrastructure.utils.security import hash_password, verify_password


def test_password_hash_roundtrip():
  hashed = hash_password("secret")
  assert hashed != "secret"
  assert verify_password("secret", hashed)
  assert not verify_password("wrong", hashed)
  assert not verify_password("secret", "basura")


def test_register_sets_cookie_and_hashes_password(client, payload, repository):
  response = client.post("/api/v1/auth/register", json=payload)
  assert response.status_code == 201
  assert response.cookies["access_token"]
  assert "httponly" in response.headers["set-cookie"].lower()
  assert response.json()["user"]["email"] == payload["email"]

  stored = list(repository.users.values())[0]
  assert stored.password != payload["password"]


def test_register_without_phone(client, payload):
  del payload["phone"]
  response = client.post("/api/v1/auth/register", json=payload)
  assert response.status_code == 201, response.text
  assert response.json()["user"]["phone"] is None


def test_register_rejects_duplicate_email(client, payload):
  client.post("/api/v1/auth/register", json=payload)
  assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_login_ok_and_me(client, payload):
  client.post("/api/v1/auth/register", json=payload)
  response = client.post(
    "/api/v1/auth/login",
    json={"email": payload["email"], "password": payload["password"]},
  )
  assert response.status_code == 200

  # El cliente reenvía la cookie solo, como el navegador
  me = client.get("/api/v1/auth/me")
  assert me.status_code == 200
  assert me.json()["email"] == payload["email"]


def test_logout_clears_session(client, payload):
  client.post("/api/v1/auth/register", json=payload)
  assert client.post("/api/v1/auth/logout").status_code == 204
  assert client.get("/api/v1/auth/me").status_code == 401


def test_login_wrong_password(client, payload):
  client.post("/api/v1/auth/register", json=payload)
  response = client.post(
    "/api/v1/auth/login", json={"email": payload["email"], "password": "nope"}
  )
  assert response.status_code == 401


def test_me_rejects_bad_cookie(client):
  client.cookies.set("access_token", "basura")
  assert client.get("/api/v1/auth/me").status_code == 401


def test_me_rejects_missing_cookie(client):
  assert client.get("/api/v1/auth/me").status_code == 401
