import os
import logfire

from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from datetime import datetime, UTC

from fastapi import FastAPI
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from joserfc.errors import JoseError

from src.container import Container
from src.infrastructure.utils.security import COOKIE_NAME, JWT_TTL, create_token, decode_token, set_session_cookie
from src.infrastructure.driving import gmail_webhook, outlook_webhook
from src.presentation.api.router import assistant, attachment, auth, contact, graph, integration, message, subscription, task, transcription, usage, user

container = Container()


@asynccontextmanager
async def lifespan(app: FastAPI):
  """El contenedor abre y cierra sus recursos (ver mongo_client en container.py)."""
  await container.init_resources()
  yield
  await container.shutdown_resources()


app = FastAPI(lifespan=lifespan)
app.container = container
app.include_router(user.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(integration.router, prefix="/api/v1")
app.include_router(gmail_webhook.router, prefix="/api/v1")
app.include_router(outlook_webhook.router, prefix="/api/v1")
app.include_router(contact.router, prefix="/api/v1")
app.include_router(task.router, prefix="/api/v1")
app.include_router(message.router, prefix="/api/v1")
app.include_router(attachment.router, prefix="/api/v1")
app.include_router(subscription.router, prefix="/api/v1")
app.include_router(graph.router, prefix="/api/v1")
app.include_router(usage.router, prefix="/api/v1")
app.include_router(assistant.router, prefix="/api/v1")
app.include_router(transcription.router, prefix="/api/v1")

# Add Logfire: separa los logs por entorno (local / prod) según ENVIRONMENT en .env
logfire.configure(environment=os.getenv("ENV"))
logfire.instrument_system_metrics()
logfire.instrument_fastapi(app)


@app.middleware("http")
async def renew_session(request, call_next):
  """Sesión deslizante: si el JWT de la cookie pasó la mitad de su vida (o caducó dentro
  de la gracia de decode_token), se reemite. Solo un token roto o muy viejo da 401."""
  response = await call_next(request)
  token = request.cookies.get(COOKIE_NAME)
  if token:
    try:
      claims = decode_token(token)
      # ponytail: sin lista de revocación; cerrar sesión = borrar la cookie
      remaining = claims["exp"] - datetime.now(UTC).timestamp()
      if remaining < JWT_TTL.total_seconds() / 2:
        set_session_cookie(response, create_token(claims["sub"]))
        logfire.info("Session renewed for {user_id} ({remaining}s left)", user_id=claims["sub"], remaining=int(remaining))
    except (JoseError, KeyError) as error:
      # roto o caducado fuera de la gracia: el guard ya devolvió 401
      logfire.warning("Session cookie rejected: {error}", error=repr(error))
  return response


# Sesión firmada (itsdangerous): la necesita el flujo OAuth de Authlib para el state
app.add_middleware(
    SessionMiddleware, secret_key=os.getenv("SESSION_SECRET")
)

# Railway corta el TLS en su proxy y nos habla en HTTP: sin esto request.url_for()
# devuelve http:// y Google rechaza el redirect_uri.
# ponytail: trusted_hosts="*" confía en cualquier X-Forwarded-Proto; en Railway el
# contenedor solo es alcanzable vía su proxy. Restringir si se expone el puerto.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

app.add_middleware(
    CORSMiddleware,
    # Con allow_credentials no vale "*": el navegador exige el origen exacto
    allow_origins=os.getenv("CORS_ORIGINS").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST","DELETE", "PUT", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "User-Agent",
        "DNT",
        "Cache-Control",
        "X-Requested-With",
    ],
)

@app.get("/")
def root():
    return {"docs": "/docs", "health": "/health", "api": "/api/v1"}

@app.get("/health")
def health():
    return {"status": "Cunplo API is healthy!"}

if __name__ == "__main__":
    uvicorn.run("src.main:app", reload=True, loop="uvloop", http="httptools")
