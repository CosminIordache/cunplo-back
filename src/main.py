import logging
import os

from dotenv import load_dotenv
load_dotenv()

# Sin esto uvicorn silencia el logger raíz y no ves nada en la terminal
logging.basicConfig(
  level=os.getenv("LOG_LEVEL", "INFO"),
  format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
  datefmt="%H:%M:%S",
)

from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from src.container import Container
from src.infrastructure.driving import gmail_webhook
from src.presentation.api.router import auth, contact, integration, user

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
app.include_router(contact.router, prefix="/api/v1")

# Sesión firmada (itsdangerous): la necesita el flujo OAuth de Authlib para el state
app.add_middleware(
    SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev-secret-change-me")
)

app.add_middleware(
    CORSMiddleware,
    # Con allow_credentials no vale "*": el navegador exige el origen exacto
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
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
