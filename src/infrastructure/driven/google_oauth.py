"""Cliente OAuth2 de Google. Authlib descubre endpoints y valida el id_token."""
import os

from authlib.integrations.starlette_client import OAuth

LOGIN_SCOPE = "openid email profile"
GMAIL_SCOPE = f"{LOGIN_SCOPE} https://www.googleapis.com/auth/gmail.readonly"

oauth = OAuth()
oauth.register(
  name="google",
  client_id=os.getenv("GOOGLE_CLIENT_ID"),
  client_secret=os.getenv("GOOGLE_SECRET"),
  server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
  client_kwargs={"scope": LOGIN_SCOPE},
)

google = oauth.google


async def refresh_token(provider, refresh_token: str) -> dict:
  """Canjea un refresh_token por un access_token nuevo. Lanza OAuthError si ya no vale."""
  metadata = await google.load_server_metadata()
  return await google.fetch_access_token(
    url=metadata["token_endpoint"],
    grant_type="refresh_token",
    refresh_token=refresh_token,
  )
