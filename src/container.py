import logfire
import os
from dependency_injector import containers, providers
from motor.motor_asyncio import AsyncIOMotorClient

from src.application.use_cases.agent_service import AgentService
from src.application.use_cases.attachment_service import AttachmentService
from src.application.use_cases.auth_service import AuthService
from src.application.use_cases.contact_service import ContactService
from src.application.use_cases.gmail_service import GmailService
from src.application.use_cases.graph_service import GraphService
from src.application.use_cases.outlook_service import OutlookService
from src.application.use_cases.integration_service import IntegrationService
from src.application.use_cases.message_service import MessageService
from src.application.use_cases.subscription_service import SubscriptionService
from src.application.use_cases.task_service import TaskService
from src.application.use_cases.usage_service import UsageService
from src.application.use_cases.user_service import UserService
from src.infrastructure.driven.mongo.mongo_attachment_repository import MongoAttachmentRepository
from src.infrastructure.driven.mongo.mongo_contact_repository import MongoContactRepository
from src.infrastructure.driven.mongo.mongo_graph_repository import MongoGraphRepository
from src.infrastructure.driven.mongo.mongo_integration_repository import MongoIntegrationRepository
from src.infrastructure.driven.mongo.mongo_message_repository import MongoMessageRepository
from src.infrastructure.driven.mongo.mongo_subscription_repository import MongoSubscriptionRepository
from src.infrastructure.driven.mongo.mongo_task_repository import MongoTaskRepository
from src.infrastructure.driven.mongo.mongo_usage_repository import MongoUsageRepository
from src.infrastructure.driven.mongo.mongo_user_repository import MongoUserRepository
from src.infrastructure.driven.redis.worker import redis_pool
from src.infrastructure.driven.s3.storage import S3Storage
from src.infrastructure.external_services.oauth_refresh import refresh_token


async def create_indexes(db) -> None:
  """create_index es idempotente: se puede llamar en cada arranque."""
  await db["users"].create_index("email", unique=True)
  # la identidad de login es el 'sub' del proveedor, no el email
  await db["users"].create_index(
    [("auth_provider", 1), ("auth_account_id", 1)], unique=True, sparse=True
  )
  # la regla: una integración por usuario, provider y cuenta (multicuenta)
  await db["integrations"].create_index(
    [("user_id", 1), ("provider", 1), ("account_id", 1)], unique=True
  )
  # el webhook de Gmail busca por email; no es único, la misma cuenta vale para varios usuarios
  await db["integrations"].create_index([("provider", 1), ("email", 1)])
  # el webhook de Graph solo trae el id de la subscription
  # único: con varias cuentas de Microsoft dos filas no pueden compartir subscription
  # parcial y no sparse: el campo existe con null en las filas sin suscripción (Gmail, o
  # Microsoft antes del primer start_subscription) y sparse solo excluye el campo ausente
  await db["integrations"].create_index(
    "subscription_id",
    unique=True,
    partialFilterExpression={"subscription_id": {"$type": "string"}},
  )
  # el id de Gmail solo es único dentro de una cuenta: la pareja evita duplicar
  await db["messages"].create_index([("integration_id", 1), ("provider_id", 1)], unique=True)
  # el hilo se lee ordenado por fecha, acotado a la cuenta
  await db["messages"].create_index(
    [("user_id", 1), ("integration_id", 1), ("thread_id", 1), ("internal_date", 1)]
  )
  # la regla: un hilo, una tarea. La cuenta entra en la clave porque el thread_id
  # solo es único dentro de ella, igual que el provider_id en messages
  await db["tasks"].create_index(
    [("user_id", 1), ("integration_id", 1), ("thread_id", 1)], unique=True
  )
  # las tres columnas de la app: tareas del usuario por estado
  await db["tasks"].create_index([("user_id", 1), ("status", 1), ("due_at", 1)])
  # un contacto por usuario y email: el mismo email puede ser cliente de dos usuarios
  await db["contacts"].create_index([("user_id", 1), ("email", 1)], unique=True)
  # el $lookup del grafo casa solo por email; sin este índice escanea todo contacts
  await db["contacts"].create_index("email")
  # el id del adjunto solo es único dentro de su mensaje: la pareja evita duplicar
  # al reprocesarse el mismo correo
  await db["attachments"].create_index([("message_id", 1), ("attachment_id", 1)], unique=True)
  # el borrado en cascada busca por usuario y por los mensajes del hilo
  await db["attachments"].create_index([("user_id", 1), ("message_id", 1)])
  # una suscripción por usuario: el histórico de cobros lo llevará la pasarela
  await db["subscriptions"].create_index("user_id", unique=True)
  # el gasto se suma por usuario, normalmente acotado a un periodo
  await db["usages"].create_index([("user_id", 1), ("created_at", -1)])


async def mongo_client(uri: str, db_name: str):
  """Ciclo de vida del cliente: el contenedor lo abre al iniciar y lo cierra al parar."""
  client = AsyncIOMotorClient(uri)
  try:
    await client.admin.command("ping")  # Motor conecta en diferido: forzamos la conexión
    logfire.info("MongoDB connected -> {uri}", uri=uri)
    await create_indexes(client[db_name])
  except Exception as error:
    logfire.error("MongoDB unavailable ({uri}): {error}", uri=uri, error=error)
  yield client
  client.close()


class Container(containers.DeclarativeContainer):
  wiring_config = containers.WiringConfiguration(
    modules=[
      "src.presentation.api.router.user",
      "src.presentation.api.router.auth",
      "src.presentation.api.router.integration",
      "src.presentation.api.router.contact",
      "src.presentation.api.router.task",
      "src.presentation.api.router.message",
      "src.presentation.api.router.attachment",
      "src.presentation.api.router.subscription",
      "src.presentation.api.router.graph",
      "src.presentation.api.router.usage",
      "src.presentation.middleware.auth",

      "src.infrastructure.driving.gmail_webhook",
      "src.infrastructure.driving.outlook_webhook",
    ]
  )

  config = providers.Configuration()
  config.mongo_uri.from_env("MONGO_URI")
  config.mongo_db.from_env("MONGO_DB")

  client = providers.Resource(mongo_client, config.mongo_uri, config.mongo_db)
  queue = providers.Resource(redis_pool)
  db = providers.Singleton(lambda c, name: c[name], client, config.mongo_db)

  # credenciales del bucket de Railway (S3-compatible)
  config.s3_endpoint.from_env("AWS_ENDPOINT_URL", "")
  config.s3_region.from_env("AWS_REGION", "auto")
  config.s3_access_key.from_env("AWS_ACCESS_KEY_ID", "")
  config.s3_secret_key.from_env("AWS_SECRET_ACCESS_KEY", "")
  config.s3_bucket.from_env("BUCKET_NAME", "")
  storage = providers.Singleton(
    S3Storage,
    endpoint_url=config.s3_endpoint,
    region=config.s3_region,
    access_key=config.s3_access_key,
    secret_key=config.s3_secret_key,
    bucket=config.s3_bucket,
  )

  user_repository = providers.Factory(MongoUserRepository, db=db)
  user_service = providers.Factory(
    UserService, repository=user_repository, storage=storage
  )
  auth_service = providers.Factory(AuthService, users=user_service)

  integration_repository = providers.Factory(MongoIntegrationRepository, db=db)
  integration_service = providers.Factory(
    IntegrationService, repository=integration_repository, refresh=refresh_token
  )

  attachment_repository = providers.Factory(MongoAttachmentRepository, db=db)
  attachment_service = providers.Factory(
    AttachmentService,
    repository=attachment_repository,
    storage=storage,
    integrations=integration_service,
  )

  message_repository = providers.Factory(MongoMessageRepository, db=db)
  message_service = providers.Factory(
    MessageService, repository=message_repository, attachments=attachment_service
  )

  task_repository = providers.Factory(MongoTaskRepository, db=db)
  task_service = providers.Factory(
    TaskService, repository=task_repository, messages=message_service
  )

  graph_repository = providers.Factory(MongoGraphRepository, db=db)
  graph_service = providers.Factory(GraphService, repository=graph_repository)

  contact_repository = providers.Factory(MongoContactRepository, db=db)
  contact_service = providers.Factory(ContactService, repository=contact_repository)

  subscription_repository = providers.Factory(MongoSubscriptionRepository, db=db)
  subscription_service = providers.Factory(
    SubscriptionService, repository=subscription_repository
  )

  usage_repository = providers.Factory(MongoUsageRepository, db=db)
  usage_service = providers.Factory(UsageService, repository=usage_repository)

  agent_service = providers.Factory(AgentService, usage_service=usage_service)

  # vacío desactiva el push de Outlook, como PUBSUB_TOPIC con Gmail
  config.graph_notification_url.from_env("GRAPH_NOTIFICATION_URL", "")
  config.graph_client_state.from_env("GRAPH_CLIENT_STATE", "")
  outlook_service = providers.Factory(
    OutlookService,
    repository=integration_repository,
    integrations=integration_service,
    notification_url=config.graph_notification_url,
    secret=config.graph_client_state,
  )

  config.pubsub_topic.from_env("PUBSUB_TOPIC", "")
  gmail_service = providers.Factory(
    GmailService,
    repository=integration_repository,
    integrations=integration_service,
    topic=config.pubsub_topic,
  )
