from datetime import datetime, timedelta, UTC

import pytest
from authlib.integrations.base_client import OAuthError
from bson import ObjectId

from src.domain.integration import Provider
from src.application.use_cases.auth_service import AuthService
from src.application.use_cases.integration_service import IntegrationService, ReauthRequired
from src.application.use_cases.user_service import UserService
from src.infrastructure.utils.crypto import decrypt, encrypt

CLAIMS = {"sub": "google-123", "email": "ada@example.com", "name": "Ada"}


def _in(seconds: int) -> float:
  return (datetime.now(UTC) + timedelta(seconds=seconds)).timestamp()


def test_is_expired_handles_naive_dates_from_mongo():
  """Mongo devuelve datetimes sin zona: comparar no debe reventar."""
  from src.domain.integration import Integration

  def build(expires_at):
    return Integration(
      user_id=ObjectId(), provider=Provider.GOOGLE, account_id="g", email="a@b.c",
      scopes=[], refresh_token="r", access_token="a", expires_at=expires_at,
    )

  naive_future = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
  naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)

  assert build(naive_future).is_expired() is False
  assert build(naive_past).is_expired() is True
  assert build(None).is_expired() is True


def make_service(repository, refresh=None) -> IntegrationService:
  async def unused(provider, token):
    raise AssertionError("no debería refrescar")

  return IntegrationService(repository, refresh=refresh or unused)


def test_crypto_roundtrip():
  assert decrypt(encrypt("secreto")) == "secreto"
  assert encrypt(None) is None and decrypt(None) is None


async def test_login_google_creates_then_reuses_user(repository):
  service = AuthService(UserService(repository))

  user, token = await service.login_google(CLAIMS)
  assert user.email == CLAIMS["email"]
  assert user.password is None
  assert token

  again, _ = await service.login_google(CLAIMS)
  assert again.id == user.id
  assert len(repository.users) == 1


async def test_connect_keeps_refresh_token_on_reconnect(integration_repository):
  service = make_service(integration_repository)
  user_id = ObjectId()

  first = await service.connect(
    user_id, Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"],
    ["gmail.readonly"], {"access_token": "a1", "refresh_token": "r1"},
  )
  assert first.refresh_token == "r1"

  # Google no reenvía refresh_token en consentimientos posteriores
  second = await service.connect(
    user_id, Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"],
    ["gmail.readonly"], {"access_token": "a2"},
  )
  assert second.refresh_token == "r1"
  assert second.access_token == "a2"
  assert second.id == first.id
  assert len(integration_repository.integrations) == 1


async def test_access_token_reuses_valid_token(integration_repository):
  # el refresh por defecto revienta: si se llama, el test falla
  service = make_service(integration_repository)
  saved = await service.connect(
    ObjectId(), Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"], [],
    {"access_token": "vigente", "refresh_token": "r1", "expires_at": _in(3600)},
  )
  assert await service.access_token_for(saved) == "vigente"


async def test_access_token_refreshes_when_expired(integration_repository):
  calls = []

  async def refresh(provider, token):
    calls.append((provider, token))
    return {"access_token": "nuevo", "expires_at": _in(3600)}

  service = make_service(integration_repository, refresh)
  saved = await service.connect(
    ObjectId(), Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"], [],
    {"access_token": "viejo", "refresh_token": "r1", "expires_at": _in(-10)},
  )

  assert await service.access_token_for(saved) == "nuevo"
  assert calls == [(Provider.GOOGLE, "r1")]

  # el token nuevo queda persistido y el refresh_token se conserva
  stored = await integration_repository.get_by_account(Provider.GOOGLE, CLAIMS["sub"])
  assert stored.access_token == "nuevo"
  assert stored.refresh_token == "r1"
  assert not stored.is_expired()


async def test_access_token_refreshes_when_token_missing(integration_repository):
  """No caducado pero sin access_token: renueva en vez de devolver None."""
  async def refresh(provider, token):
    return {"access_token": "nuevo", "expires_at": _in(3600)}

  service = make_service(integration_repository, refresh)
  saved = await service.connect(
    ObjectId(), Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"], [],
    {"refresh_token": "r1", "expires_at": _in(3600)},
  )
  assert saved.access_token is None
  assert await service.access_token_for(saved) == "nuevo"


async def test_access_token_raises_when_refresh_revoked(integration_repository):
  async def revoked(provider, token):
    raise OAuthError("invalid_grant")

  service = make_service(integration_repository, revoked)
  saved = await service.connect(
    ObjectId(), Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"], [],
    {"access_token": "viejo", "refresh_token": "r1", "expires_at": _in(-10)},
  )
  with pytest.raises(ReauthRequired):
    await service.access_token_for(saved)


async def test_access_token_raises_without_refresh_token(integration_repository):
  service = make_service(integration_repository)
  saved = await service.connect(
    ObjectId(), Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"], [],
    {"access_token": "viejo", "expires_at": _in(-10)},
  )
  with pytest.raises(ReauthRequired):
    await service.access_token_for(saved)

async def test_refresh_asks_for_the_gmail_scope(monkeypatch):
  """Dos fallos vistos en producción: url= duplicada y un refresh sin el scope de Gmail
  (Google devolvía un token de solo login y Gmail respondía 403)."""
  from authlib.integrations.httpx_client import AsyncOAuth2Client
  from src.infrastructure.driven import google_oauth

  seen = {}

  async def fake_post(self, url, **kwargs):
    seen.update(url=url, data=kwargs.get("data"))
    import httpx

    return httpx.Response(
      200,
      json={"access_token": "nuevo", "expires_in": 3599},
      request=httpx.Request("POST", url),
    )

  async def metadata(self):
    return {"token_endpoint": "https://oauth2.googleapis.com/token"}

  monkeypatch.setattr(type(google_oauth.google), "load_server_metadata", metadata)
  monkeypatch.setattr(AsyncOAuth2Client, "post", fake_post)

  token = await google_oauth.refresh_token(Provider.GOOGLE, "rt-viejo")

  assert token["access_token"] == "nuevo"
  assert seen["url"] == "https://oauth2.googleapis.com/token"
  assert seen["data"]["refresh_token"] == "rt-viejo"
  assert "gmail.readonly" in seen["data"]["scope"]


async def test_connect_with_another_account_replaces_it(integration_repository):
  """Una integración por usuario y provider: la segunda cuenta sustituye a la primera."""
  service = make_service(integration_repository)
  user_id = ObjectId()
  first = await service.connect(
    user_id, Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"], [],
    {"access_token": "a", "refresh_token": "rt-1"},
  )
  first.history_id = "100"
  await integration_repository.upsert(first)

  second = await service.connect(
    user_id, Provider.GOOGLE, "google-999", "otra@example.com", [],
    {"access_token": "b", "refresh_token": "rt-2"},
  )

  assert await integration_repository.list_by_user(user_id) == [second]
  assert second.id == first.id  # misma fila
  assert second.refresh_token == "rt-2"
  assert second.history_id is None  # el buzón es otro: no heredamos la sincronización


async def test_connect_is_per_user(integration_repository):
  """La misma cuenta de Google en dos usuarios son dos filas."""
  service = make_service(integration_repository)
  one, two = ObjectId(), ObjectId()
  await service.connect(
    one, Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"], [], {"access_token": "a"}
  )
  await service.connect(
    two, Provider.GOOGLE, CLAIMS["sub"], CLAIMS["email"], [], {"access_token": "b"}
  )

  assert len(await integration_repository.list_by_user(one)) == 1
  assert len(await integration_repository.list_by_user(two)) == 1


# desconectar vive ahora en GmailService (para el watch y revoca): ver test_gmail_push.py
