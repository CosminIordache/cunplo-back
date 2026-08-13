import os
from dependency_injector import containers, providers
from motor.motor_asyncio import AsyncIOMotorClient

from src.application.use_cases.auth_service import AuthService
from src.application.use_cases.integration_service import IntegrationService
from src.application.use_cases.user_service import UserService
from src.infrastructure.driven.google_oauth import refresh_token
from src.infrastructure.driven.mongo_integration_repository import MongoIntegrationRepository
from src.infrastructure.driven.mongo_user_repository import MongoUserRepository


class Container(containers.DeclarativeContainer):
  wiring_config = containers.WiringConfiguration(
    modules=[
      "src.presentation.api.router.user",
      "src.presentation.api.router.auth",
      "src.presentation.api.router.integration",
      "src.presentation.middleware.auth",
    ]
  )

  config = providers.Configuration()
  config.mongo_uri.from_env("MONGO_URI", "mongodb://localhost:27017")
  config.mongo_db.from_env("MONGO_DB", "cunplo")

  client = providers.Singleton(AsyncIOMotorClient, config.mongo_uri)
  db = providers.Singleton(lambda c, name: c[name], client, config.mongo_db)

  user_repository = providers.Factory(MongoUserRepository, db=db)
  user_service = providers.Factory(UserService, repository=user_repository)
  auth_service = providers.Factory(AuthService, users=user_service)

  integration_repository = providers.Factory(MongoIntegrationRepository, db=db)
  integration_service = providers.Factory(
    IntegrationService, repository=integration_repository, refresh=refresh_token
  )
