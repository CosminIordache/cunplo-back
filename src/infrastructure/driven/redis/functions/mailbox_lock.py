from contextlib import asynccontextmanager

import logfire
from arq import Retry
from redis.exceptions import LockError

# el lock caduca a la vez que el job (job_timeout por defecto de arq): un worker que
# muere con el lock cogido no deja el buzón bloqueado para siempre
LOCK_SECONDS = 300
WAIT_SECONDS = 10
# intentos para esperar al que tiene el lock durante todo su job_timeout, y algo más
MAX_TRIES = LOCK_SECONDS // WAIT_SECONDS + 10


@asynccontextmanager
async def mailbox_lock(ctx, key: str):
  """Un solo job por buzón a la vez. Gmail manda un aviso por cada cambio y los jobs
  en paralelo leían el mismo marcador: todos descargaban los mismos correos y agotaban
  la cuota. El que espera se reintenta y, cuando entra, el marcador ya avanzó: solo
  le queda lo que llegó después, que es barato."""
  lock = ctx["redis"].lock(f"mailbox-lock:{key}", timeout=LOCK_SECONDS)
  if not await lock.acquire(blocking=False):
    raise Retry(defer=WAIT_SECONDS)
  try:
    yield
  finally:
    try:
      await lock.release()
    except LockError:
      # caducó mientras corría (job más largo que LOCK_SECONDS): ya no es nuestro
      logfire.warning("Mailbox lock {key} expired before release", key=key)
