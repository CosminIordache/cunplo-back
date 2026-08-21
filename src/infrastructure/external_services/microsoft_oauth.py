import os

from authlib.integrations.starlette_client import OAuth

LOGIN_SCOPE = "openid email profile offline_access"
MAIL_SCOPE = f"{LOGIN_SCOPE} https://graph.microsoft.com/Mail.Read"

oauth = OAuth()
oauth.register(
  name="microsoft",
  client_id=os.getenv("MS_CLIENT_ID"),
  client_secret=os.getenv("MS_SECRET"),
  # 'common' porque la app es multiinquilino: acepta cuentas de empresa y personales
  server_metadata_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
  client_kwargs={"scope": MAIL_SCOPE},
)

# Con 'common' el metadata declara el issuer como plantilla ({tenantid}) y el id_token trae
# el tenant real, así que la comparación literal de Authlib falla. Sin 'values' joserfc solo
# comprueba que el claim exista; qué cuentas entran lo acota el signInAudience de la app.
CLAIMS_OPTIONS = {"iss": {"essential": True}}

microsoft = oauth.microsoft


async def refresh_token(refresh_token: str) -> dict:
  """Canjea un refresh_token por uno nuevo. Lanza OAuthError si ya no vale."""
  return await microsoft.fetch_access_token(
    grant_type="refresh_token",
    refresh_token=refresh_token,
  )

# ponytail: sin revoke: Microsoft no expone endpoint de revocación por token,
# solo el borrado del consentimiento desde la cuenta del usuario.
