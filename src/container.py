import logging
import os
from dependency_injector import containers, providers
from motor.motor_asyncio import AsyncIOMotorClient

from src.application.use_cases.auth_service import AuthService
from src.application.use_cases.gmail_service import GmailService
from src.application.use_cases.integration_service import IntegrationService
from src.application.use_cases.user_service import UserService
from src.infrastructure.driven.google_oauth import refresh_token
from src.infrastructure.driven.mongo_integration_repository import MongoIntegrationRepository
from src.infrastructure.driven.mongo_user_repository import MongoUserRepository


async def create_indexes(db) -> None:
  """create_index es idempotente: se puede llamar en cada arranque."""
  await db["users"].create_index("email", unique=True)
  # la regla: una integración por usuario y provider
  await db["integrations"].create_index([("user_id", 1), ("provider", 1)], unique=True)
  # el webhook de Gmail busca por email; no es único, la misma cuenta vale para varios usuarios
  await db["integrations"].create_index([("provider", 1), ("email", 1)])


async def mongo_client(uri: str, db_name: str):
  """Ciclo de vida del cliente: el contenedor lo abre al iniciar y lo cierra al parar."""
  client = AsyncIOMotorClient(uri)
  try:
    await client.admin.command("ping")  # Motor conecta en diferido: forzamos la conexión
    logging.info("MongoDB connected -> %s", uri)
    await create_indexes(client[db_name])
  except Exception as error:
    logging.error("MongoDB unavailable (%s): %s", uri, error)
  yield client
  client.close()


class Container(containers.DeclarativeContainer):
  wiring_config = containers.WiringConfiguration(
    modules=[
      "src.presentation.api.router.user",
      "src.presentation.api.router.auth",
      "src.presentation.api.router.integration",
      "src.presentation.api.router.webhook",
      "src.presentation.middleware.auth",
    ]
  )

  config = providers.Configuration()
  config.mongo_uri.from_env("MONGO_URI", "mongodb://localhost:27017")
  config.mongo_db.from_env("MONGO_DB", "cunplo")

  client = providers.Resource(mongo_client, config.mongo_uri, config.mongo_db)
  db = providers.Singleton(lambda c, name: c[name], client, config.mongo_db)

  user_repository = providers.Factory(MongoUserRepository, db=db)
  user_service = providers.Factory(UserService, repository=user_repository)
  auth_service = providers.Factory(AuthService, users=user_service)

  integration_repository = providers.Factory(MongoIntegrationRepository, db=db)
  integration_service = providers.Factory(
    IntegrationService, repository=integration_repository, refresh=refresh_token
  )

  config.pubsub_topic.from_env("PUBSUB_TOPIC", "")
  gmail_service = providers.Factory(
    GmailService,
    repository=integration_repository,
    integrations=integration_service,
    topic=config.pubsub_topic,
  )
