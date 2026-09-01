import os
import logfire
from arq import cron
from arq.connections import RedisSettings, create_pool

from src.infrastructure.driven.redis.functions.process_gmail_notification import (
  process_gmail_notification,
)
from src.infrastructure.driven.redis.functions.process_outlook_sync import (
  process_outlook_sync,
)
from src.infrastructure.driven.redis.functions.renew_watches import renew_watches

from dotenv import load_dotenv
load_dotenv()

REDIS = RedisSettings.from_dsn(os.getenv("REDIS_URI", "redis://localhost:6379"))


async def redis_pool():
  """Ciclo de vida del pool: el contenedor lo abre al iniciar y lo cierra al parar."""
  # create_pool ya hace ping: si Redis no está, lanza aquí mismo
  try:
    pool = await create_pool(REDIS)
    logfire.info("Redis connected -> {host}:{port}", host=REDIS.host, port=REDIS.port)
  except Exception as error:
    logfire.error("Redis unavailable ({host}:{port}): {error}", host=REDIS.host, port=REDIS.port, error=error)
    pool = None
  yield pool
  if pool:
    await pool.aclose()


async def startup(ctx) -> None:

  from src.container import Container

  # El worker es otro proceso: necesita su propio configure para enviar a Logfire
  logfire.configure(environment=os.getenv("ENV"), service_name="arq-worker")

  container = Container()
  await container.init_resources()
  ctx["container"] = container

  for name in (
    "gmail_service",
    "outlook_service",
    "integration_service",
    "integration_repository",
    "message_service",
    "attachment_service",
    "task_service",
    "contact_service",
    "user_service",
    "agent_service",
  ):
    ctx[name] = await getattr(container, name)()
  logfire.info("ARQ worker started!")


async def shutdown(ctx) -> None:
  await ctx["container"].shutdown_resources()


class WorkerSettings:
  """Arranca con: uv run arq src.infrastructure.driven.redis.worker.WorkerSettings"""

  functions = [process_gmail_notification, process_outlook_sync]
  # Graph solo da ~3 días de subscription: diario a las 4:00 va sobrado
  cron_jobs = [cron(renew_watches, hour=4, minute=0)]
  redis_settings = REDIS
  on_startup = startup
  on_shutdown = shutdown
  max_tries = 3
  # ponytail: un worker, concurrencia por defecto (10); sube job_timeout/max_jobs si hace falta
