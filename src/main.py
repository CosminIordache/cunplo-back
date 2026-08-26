import os
import logfire

from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.container import Container
from src.infrastructure.driving import gmail_webhook, outlook_webhook
from src.presentation.api.router import attachment, auth, contact, integration, message, task, user

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

# Add Logfire: separa los logs por entorno (local / prod) según ENVIRONMENT en .env
logfire.configure(environment=os.getenv("ENV"))
logfire.instrument_system_metrics()
logfire.instrument_fastapi(app)


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
