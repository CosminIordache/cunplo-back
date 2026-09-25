import hashlib
import hmac
import json
import os
from typing import Annotated

import logfire
from arq import ArqRedis
from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, Request, Response, status

from src.application.ports.integration_repository import IntegrationRepository
from src.container import Container
from src.domain.integration import Provider
from src.infrastructure.external_services.gowa import jid_to_phone, to_message

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

Queue = Annotated[ArqRedis, Depends(Provide[Container.queue])]
Repository = Annotated[IntegrationRepository, Depends(Provide[Container.integration_repository])]

# chats que no son una conversación con una persona: grupos, estados y canales
IGNORED_CHATS = ("@g.us", "@broadcast", "@newsletter")


def valid_signature(body: bytes, header: str | None, secret: str) -> bool:
  """GOWA firma el cuerpo en bruto con HMAC-SHA256: 'sha256=<hex>' en X-Hub-Signature-256."""
  expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
  return hmac.compare_digest(expected, header or "")


@router.post("/whatsapp")
@inject
async def whatsapp_push(request: Request, queue: Queue, repository: Repository):
  """GOWA reintenta si respondemos != 2xx, así que devolvemos 200 casi siempre."""
  body = await request.body()
  # sin secreto no hay forma de saber que el aviso viene de GOWA: se rechaza todo
  secret = os.getenv("GOWA_WEBHOOK_SECRET", "")
  if not (secret and valid_signature(body, request.headers.get("X-Hub-Signature-256"), secret)):
    logfire.warning("WhatsApp notification with bad signature, ignored")
    return Response(status_code=status.HTTP_200_OK)

  event = json.loads(body)
  payload = event.get("payload") or {}
  # los mensajes del propio dueño también se analizan: su "te lo envío mañana" cambia la tarea
  if event.get("event") != "message":
    return Response(status_code=status.HTTP_200_OK)
  if (payload.get("chat_id") or "").endswith(IGNORED_CHATS):
    return Response(status_code=status.HTTP_200_OK)
  # ponytail: solo texto; notas de voz (transcription_service) y adjuntos, más adelante
  if not payload.get("body"):
    return Response(status_code=status.HTTP_200_OK)

  # session_id es el device_id que registramos, nuestro account_id. Si GOWA no lo
  # puede mapear, queda device_id: el JID del número, que es el email de la fila.
  # Sin integración se ignora: aquí no se crea (resucitaría una cuenta desconectada con
  # GOWA caído); la crea /status mientras el usuario vincula
  integration = None
  if event.get("session_id"):
    integration = await repository.get_by_account(Provider.WHATSAPP, event["session_id"])
  phone = jid_to_phone(event.get("device_id") or "")
  if not integration and phone:
    integration = await repository.get_by_email(Provider.WHATSAPP, phone)
  if not integration:
    logfire.warning(
      "WhatsApp message for device {device_id}: not connected, ignored",
      device_id=event.get("session_id") or event.get("device_id"),
    )
    return Response(status_code=status.HTTP_200_OK)

  message = to_message(payload, integration.email)
  # el _job_id deduplica: GOWA reintenta el mismo mensaje y no queremos pagar dos veces el LLM
  await queue.enqueue_job(
    "process_whatsapp_message",
    str(integration.id),
    str(integration.user_id),
    message,
    _job_id=f"wa:{integration.id}:{message['id']}",
  )
  return Response(status_code=status.HTTP_200_OK)
