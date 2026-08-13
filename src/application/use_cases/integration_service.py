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
  
    existing = await self.repository.get_by_account(provider, account_id)
    refresh_token = token.get("refresh_token") or (existing.refresh_token if existing else None)
    
    integration = Integration(
      id=existing.id if existing else ObjectId(),
      user_id=user_id,
      provider=provider,
      account_id=account_id,
      email=email,
      scopes=scopes,
      refresh_token=refresh_token,
      access_token=token.get("access_token"),
      expires_at=datetime.fromtimestamp(token["expires_at"], UTC) if token.get("expires_at") else None,
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

  async def disconnect(self, integration_id: ObjectId, user_id: ObjectId) -> bool:
    return await self.repository.delete(integration_id, user_id)
