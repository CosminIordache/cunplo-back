import json

import logfire

# estados que ve el frontend por mensaje
PROCESSING = "processing"
PROCESSED = "processed"
SKIPPED = "skipped"
# el mensaje falló a mitad (LLM, Mongo, bucket…): el detalle va a logfire, no al frontend
ERROR = "error"
# motivos de un skipped
NOT_CONTACT = "not_contact"
NO_TASK = "no_task"


def channel(user_id) -> str:
  """Canal pub/sub del usuario: el worker publica, GET /events lo reenvía por SSE."""
  return f"events:{user_id}"


async def publish(ctx, user_id, status: str, **data) -> None:
  """Avisa al frontend del usuario. Si nadie escucha se pierde (no es un histórico: lo
  que queda está en Mongo). Un fallo aquí no tumba el job, que ya gastó LLM."""
  try:
    await ctx["redis"].publish(channel(user_id), json.dumps({"status": status, **data}, default=str))
  except Exception as error:
    logfire.warning("Event {status} for user {user_id} not published: {error}", status=status, user_id=user_id, error=error)
