import os
from typing import Annotated

import logfire
from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, Request, Response, status

from src.container import Container
from src.domain.integration import Provider

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

Queue = Annotated[object, Depends(Provide[Container.queue])]
Repository = Annotated[object, Depends(Provide[Container.integration_repository])]


@router.post("/outlook")
@inject
async def outlook_push(
  request: Request, queue: Queue, repository: Repository, validationToken: str = ""
):
  """Graph reintenta si respondemos != 2xx, así que devolvemos 202 casi siempre."""
  # Al crear la subscription, Graph llama primero con ?validationToken= y espera
  # ese mismo valor en texto plano: sin esto no deja registrarla
  if validationToken:
    return Response(content=validationToken, media_type="text/plain")

  envelope = await request.json()
  secret = os.getenv("GRAPH_CLIENT_STATE")

  for notification in envelope.get("value", []):
    # clientState es lo único que autentica el aviso: Graph no firma el push
    if secret and notification.get("clientState") != secret:
      logfire.warning("Outlook notification with bad clientState, ignored")
      continue

    # el aviso solo dice 'algo cambió': el correo se lee del delta, no de aquí
    subscription_id = notification.get("subscriptionId")
    integration = await repository.get_by_subscription(subscription_id)
    if not integration:
      logfire.warning(
        "Notification for subscription {subscription_id}: not connected, ignored",
        subscription_id=subscription_id,
      )
      continue

    await queue.enqueue_job("process_outlook_sync", str(integration.user_id))

  return Response(status_code=status.HTTP_202_ACCEPTED)
