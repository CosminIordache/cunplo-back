def test_create_and_list(client, contact_id):
  contacts = client.get("/api/v1/contacts").json()
  assert [c["id"] for c in contacts] == [contact_id]


def test_get_one(client, contact_id):
  response = client.get(f"/api/v1/contacts/{contact_id}")
  assert response.status_code == 200
  assert response.json()["email"] == "cliente@example.com"


def test_duplicate_email_conflicts(client, contact_id):
  response = client.post(
    "/api/v1/contacts", json={"email": "cliente@example.com", "name": "Otra vez"}
  )
  assert response.status_code == 409


def test_same_email_other_user_is_allowed(client, contact_id, login_as_other):
  login_as_other()
  response = client.post("/api/v1/contacts", json={"email": "cliente@example.com"})
  assert response.status_code == 201


def test_update_to_existing_email_conflicts(client, contact_id):
  other = client.post("/api/v1/contacts", json={"email": "segundo@example.com"}).json()
  response = client.patch(
    f"/api/v1/contacts/{other['id']}", json={"email": "cliente@example.com"}
  )
  assert response.status_code == 409


def test_update_changes_email(client, contact_id):
  response = client.patch(
    f"/api/v1/contacts/{contact_id}", json={"email": "nuevo@example.com"}
  )
  assert response.status_code == 200
  assert response.json()["email"] == "nuevo@example.com"


def test_other_user_cannot_get(client, contact_id, login_as_other):
  login_as_other()
  assert client.get(f"/api/v1/contacts/{contact_id}").status_code == 404


def test_other_user_cannot_update(client, contact_id, login_as_other):
  login_as_other()
  response = client.patch(f"/api/v1/contacts/{contact_id}", json={"name": "mío"})
  assert response.status_code == 404


def test_other_user_cannot_delete(client, contact_id, login_as_other):
  login_as_other()
  assert client.delete(f"/api/v1/contacts/{contact_id}").status_code == 404
  # y sigue siendo del dueño
  assert client.get("/api/v1/contacts").json() == []


def test_owner_can_delete(client, contact_id):
  assert client.delete(f"/api/v1/contacts/{contact_id}").status_code == 204
  assert client.get("/api/v1/contacts").json() == []
