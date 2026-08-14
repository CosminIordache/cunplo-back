"""El callback de login deja usuario + integración con refresh_token (flujo B)."""
from datetime import datetime, timedelta, UTC

import pytest

from src.domain.integration import Provider
from src.presentation.api.router import auth

CLAIMS = {"sub": "google-123", "email": "ada@example.com", "name": "Ada"}


@pytest.fixture
def fake_google(monkeypatch):
  """Sustituye el cliente de Authlib: ni red, ni sesión, ni state."""
  captured = {}

  async def authorize_access_token(request):
    return {
      "access_token": "at-1",
      "refresh_token": "rt-1",
      "expires_at": (datetime.now(UTC) + timedelta(hours=1)).timestamp(),
      "scope": "openid email https://www.googleapis.com/auth/gmail.readonly",
      "userinfo": CLAIMS,
    }

  async def authorize_redirect(request, redirect_uri, **kwargs):
    captured.update(kwargs)
    from fastapi.responses import RedirectResponse

    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth")

  monkeypatch.setattr(auth.google, "authorize_access_token", authorize_access_token)
  monkeypatch.setattr(auth.google, "authorize_redirect", authorize_redirect)
  return captured


def test_login_asks_for_gmail_offline(client, fake_google):
  client.get("/api/v1/auth/google", follow_redirects=False)
  assert "gmail.readonly" in fake_google["scope"]
  assert fake_google["access_type"] == "offline"  # sin esto Google no da refresh_token
  assert fake_google["prompt"] == "consent"


def test_callback_creates_user_and_integration(
  client, fake_google, repository, integration_repository
):
  response = client.get("/api/v1/auth/google/callback", follow_redirects=False)
  assert response.status_code == 307
  # SessionMiddleware emite su propio Set-Cookie: la nuestra debe sobrevivir entera
  assert response.cookies["access_token"]
  assert "#token=" not in response.headers["location"]

  user = next(iter(repository.users.values()))
  assert user.email == CLAIMS["email"]

  stored = await_integration(integration_repository)
  assert stored.user_id == user.id
  assert stored.provider == Provider.GOOGLE
  assert stored.account_id == CLAIMS["sub"]
  assert stored.refresh_token == "rt-1"
  assert "https://www.googleapis.com/auth/gmail.readonly" in stored.scopes


def test_second_login_reuses_user_and_integration(
  client, fake_google, repository, integration_repository
):
  client.get("/api/v1/auth/google/callback", follow_redirects=False)
  client.get("/api/v1/auth/google/callback", follow_redirects=False)
  assert len(repository.users) == 1
  assert len(integration_repository.integrations) == 1


def await_integration(repo):
  assert len(repo.integrations) == 1, repo.integrations
  return next(iter(repo.integrations.values()))
