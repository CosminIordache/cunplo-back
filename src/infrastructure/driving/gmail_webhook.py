import base64
import json
import os
from typing import Annotated

from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from src.container import Container

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

Queue = Annotated[object, Depends(Provide[Container.queue])]


@router.post("/gmail")
@inject
async def gmail_push(request: Request, queue: Queue):
  """Pub/Sub reintenta si respondemos != 2xx, así que devolvemos 204 casi siempre."""
  # Token compartido en la query: Pub/Sub no firma el push básico
  secret = os.getenv("PUBSUB_TOKEN")
  if secret and request.query_params.get("token") != secret:
    raise HTTPException(status.HTTP_403_FORBIDDEN, "bad token")

  envelope = await request.json()
  data = envelope.get("message", {}).get("data")
  if not data:
    return Response(status_code=status.HTTP_204_NO_CONTENT)

  payload = json.loads(base64.b64decode(data))
  await queue.enqueue_job(
    "process_gmail_notification",
    payload["emailAddress"],
    str(payload["historyId"]),
  )
  return Response(status_code=status.HTTP_204_NO_CONTENT)
