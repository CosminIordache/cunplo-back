from datetime import datetime, UTC
from typing import Awaitable, Callable, Optional

from authlib.integrations.base_client import OAuthError
from bson import ObjectId

from src.domain.integration import Integration, Provider
from src.application.ports.integration_repository import IntegrationRepository

__all__ = ["IntegrationService", "ReauthRequired"]


class ReauthRequired(Exception):
  """El refresh_token ya no sirve: el usuario tiene que reconectar la cuenta."""


class IntegrationService:
  def __init__(
    self,
    repository: IntegrationRepository,
    refresh: Callable[[Provider, str], Awaitable[dict]],
  ):
    self.repository = repository
    self.refresh = refresh

  async def connect(
    self,
    user_id: ObjectId,
    provider: Provider,
    account_id: str,
    email: str,
    scopes: list[str],
    token: dict,
  ) -> Integration:
  
    # una integración por usuario y provider: la fila es siempre la misma
    row = await self.repository.get_by_user(user_id, provider)
    # cuenta distinta: reemplazamos la anterior sin heredar tokens ni sincronización
    existing = row if row and row.account_id == account_id else None
    refresh_token = token.get("refresh_token") or (existing.refresh_token if existing else None)

    integration = Integration(
      id=row.id if row else ObjectId(),
      user_id=user_id,
      provider=provider,
      account_id=account_id,
      email=email,
      scopes=scopes,
      refresh_token=refresh_token,
      access_token=token.get("access_token"),
      expires_at=datetime.fromtimestamp(token["expires_at"], UTC) if token.get("expires_at") else None,
      # estado de sincronización: sobrevive a los refrescos de token
      history_id=existing.history_id if existing else None,
      watch_expires_at=existing.watch_expires_at if existing else None,
    )
    
    return await self.repository.upsert(integration)

  async def access_token_for(self, integration: Integration) -> str:
    """Access token vigente. Renueva contra el proveedor solo si ha caducado."""
    if not integration.is_expired() and integration.access_token:
      return integration.access_token
      
    if not integration.refresh_token:
      raise ReauthRequired  # sin refresh_token no hay nada que renovar
    
    try:
      token = await self.refresh(integration.provider, integration.refresh_token)
    
    except OAuthError:
      raise ReauthRequired  # revocado o consentimiento retirado: reconectar a mano
    
    renewed = await self.connect(
      user_id=integration.user_id,
      provider=integration.provider,
      account_id=integration.account_id,
      email=integration.email,
      scopes=integration.scopes,
      token=token,
    )
    
    if not renewed.access_token:
      raise ReauthRequired  # el proveedor no devolvió token: trátalo como reconexión
    
    return renewed.access_token

  async def list_by_user(self, user_id: ObjectId) -> list[Integration]:
    return await self.repository.list_by_user(user_id)

  async def get_by_email(self, provider: Provider, email: str) -> Optional[Integration]:
    return await self.repository.get_by_email(provider, email)
