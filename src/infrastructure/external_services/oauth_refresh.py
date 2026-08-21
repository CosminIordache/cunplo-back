from src.domain.integration import Provider
from src.infrastructure.external_services.google_oauth import refresh_token as google_refresh
from src.infrastructure.external_services.microsoft_oauth import refresh_token as microsoft_refresh

REFRESHERS = {
  Provider.GOOGLE: google_refresh,
  Provider.MICROSOFT: microsoft_refresh,
}


async def refresh_token(provider: Provider, refresh_token: str) -> dict:
  """Renueva el access_token contra el proveedor que corresponda."""
  return await REFRESHERS[provider](refresh_token)
