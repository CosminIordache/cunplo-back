import os

import httpx
from authlib.integrations.starlette_client import OAuth

LOGIN_SCOPE = "openid email profile"
GMAIL_SCOPE = f"{LOGIN_SCOPE} https://www.googleapis.com/auth/gmail.readonly"

oauth = OAuth()
oauth.register(
  name="google",
  client_id=os.getenv("GOOGLE_CLIENT_ID"),
  client_secret=os.getenv("GOOGLE_SECRET"),
  server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
  # GMAIL_SCOPE y no LOGIN_SCOPE: en el refresh Authlib manda este scope por defecto,
  # y pedir menos del concedido devuelve un token sin Gmail (403 al leer el buzón)
  client_kwargs={"scope": GMAIL_SCOPE},
)

google = oauth.google


async def revoke_token(token: str) -> None:
  """Invalida el token en Google. Idempotente: revocar uno ya muerto también da 200/400."""
  async with httpx.AsyncClient(timeout=30) as http:
    await http.post("https://oauth2.googleapis.com/revoke", data={"token": token})


async def refresh_token(refresh_token: str) -> dict:
  """Canjea un refresh_token por un access_token nuevo. Lanza OAuthError si ya no vale."""
  return await google.fetch_access_token(
    grant_type="refresh_token",
    refresh_token=refresh_token,
  )
