import logging
import os
from arq.connections import RedisSettings, create_pool

from src.infrastructure.driven.redis.functions.process_gmail_notification import (
  process_gmail_notification,
)

from dotenv import load_dotenv
load_dotenv()

REDIS = RedisSettings.from_dsn(os.getenv("REDIS_URI", "redis://localhost:6379"))


async def redis_pool():
  """Ciclo de vida del pool: el contenedor lo abre al iniciar y lo cierra al parar."""
  # create_pool ya hace ping: si Redis no está, lanza aquí mismo
  try:
    pool = await create_pool(REDIS)
    logging.info("Redis connected -> %s:%s", REDIS.host, REDIS.port)
  except Exception as error:
    logging.error("Redis unavailable (%s:%s): %s", REDIS.host, REDIS.port, error)
    pool = None
  yield pool
  if pool:
    await pool.aclose()


async def startup(ctx) -> None:

  from src.container import Container

  logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
  )

  container = Container()
  await container.init_resources()
  ctx["container"] = container
  # los providers dependen de un Resource async (el cliente de Mongo): hay que esperarlos
  ctx["gmail_service"] = await container.gmail_service()
  ctx["integration_service"] = await container.integration_service()
  ctx["message_service"] = await container.message_service()
  logging.info("ARQ worker started!")


async def shutdown(ctx) -> None:
  await ctx["container"].shutdown_resources()


class WorkerSettings:
  """Arranca con: uv run arq src.infrastructure.driven.redis.worker.WorkerSettings"""

  functions = [process_gmail_notification]
  redis_settings = REDIS
  on_startup = startup
  on_shutdown = shutdown
  max_tries = 3
  # ponytail: un worker, concurrencia por defecto (10); sube job_timeout/max_jobs si hace falta
