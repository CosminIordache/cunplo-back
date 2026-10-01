from typing import Annotated, Optional

from arq import ArqRedis
from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.container import Container
from src.infrastructure.driven.redis.functions.events import channel
from src.presentation.middleware.auth import CurrentUser

router = APIRouter(prefix="/events", tags=["events"])

Queue = Annotated[Optional[ArqRedis], Depends(Provide[Container.queue])]

# comentario SSE cada tanto: mantiene viva la conexión tras el proxy de Railway
KEEPALIVE_SECONDS = 15


@router.get("")
@inject
async def events(request: Request, user: CurrentUser, queue: Queue):
  """SSE con el estado de los mensajes del usuario según los procesa el worker
  (processing / processed / skipped). El frontend: new EventSource(url, {withCredentials: true})."""
  if not queue:
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "events unavailable")

  async def stream():
    pubsub = queue.pubsub()
    try:
      await pubsub.subscribe(channel(user.id))
      while not await request.is_disconnected():
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=KEEPALIVE_SECONDS)
        yield f"data: {message['data'].decode()}\n\n" if message else ": ping\n\n"
    finally:
      await pubsub.aclose()

  return StreamingResponse(
    stream(),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
  )
