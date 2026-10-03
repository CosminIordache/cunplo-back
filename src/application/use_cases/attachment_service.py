import base64
import logfire
from typing import Optional
from bson import ObjectId

from src.application.use_cases.agent_service import AgentAttachment
from src.domain.attachment import Attachment
from src.domain.message import Message
from src.application.ports.attachment_repository import AttachmentRepository
from src.application.ports.storage import Storage
from src.application.use_cases.integration_service import ReauthRequired
from src.domain.integration import Integration
from src.application.use_cases.integration_service import IntegrationService
from src.infrastructure.external_services import gmail


# lo que el modelo puede leer; el resto le llega solo por nombre
READABLE = ("image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf")
# ponytail: tope fijo por fichero; subirlo si se escapan tareas por PDFs grandes
MAX_READ_BYTES = 10 * 1024 * 1024


def _readable(mime_type: str, size: int) -> bool:
  return mime_type in READABLE and size <= MAX_READ_BYTES


def _key(message: Message, attachment: dict) -> str:
  """Ruta dentro del bucket. Empieza por el usuario para poder borrarlo por prefijo."""
  return (
    f"{message.user_id}/{message.integration_id}/{message.provider_id}/"
    f"{attachment['attachment_id']}/{attachment['filename']}"
  )


class AttachmentService:
  def __init__(
    self,
    repository: AttachmentRepository,
    storage: Storage,
    integrations: IntegrationService,
  ):
    self.repository = repository
    self.storage = storage
    self.integrations = integrations

  async def _bytes(
    self, integration: Integration, token: str, provider_id: str, attachment: dict
  ) -> bytes:
    """Gmail obliga a una llamada por fichero.
    Si for_agent ya los bajó, se reutilizan."""
    if data := attachment.get("data"):
      return data
    return await gmail.get_attachment(token, provider_id, attachment["attachment_id"])

  async def _resolve(
    self, integration: Integration, provider_id: str, raw_message: dict
  ) -> tuple[str, list[dict]]:
    """El token y la lista de adjuntos del mensaje (Gmail los trae en el propio mensaje)."""
    attachments = raw_message.get("attachments") or []
    if not attachments:
      return "", []

    try:
      token = await self.integrations.access_token_for(integration)
    except ReauthRequired:
      logfire.warning(
        "No token for {email}: attachments of {provider_id} not fetched",
        email=integration.email,
        provider_id=provider_id,
      )
      return "", []
    return token, attachments

  async def for_agent(
    self, integration: Integration, provider_id: str, raw_message: dict
  ) -> list[AgentAttachment]:
    """Los adjuntos de un mensaje nuevo para el agente, antes de saber si se guarda.
    Los legibles se bajan y los bytes se quedan en el dict ("data"): store_for_message
    los reutiliza en vez de pedirlos otra vez."""
    token, attachments = await self._resolve(integration, provider_id, raw_message)
    result = []
    for attachment in attachments:
      if _readable(attachment["mime_type"], attachment.get("size") or 0):
        try:
          attachment["data"] = await self._bytes(integration, token, provider_id, attachment)
        except gmail.GmailRateLimited:
          raise  # el job lo convierte en Retry: el marcador no avanzó
        except Exception as error:
          # sin el contenido el agente aún tiene el nombre: no tumba el análisis
          logfire.warning(
            "Could not fetch attachment {filename} of {provider_id} for the agent: {error}",
            filename=attachment.get("filename"),
            provider_id=provider_id,
            error=str(error),
          )
      data = attachment.get("data")
      result.append(AgentAttachment(
        filename=attachment["filename"],
        mime_type=attachment["mime_type"],
        data=data if data and _readable(attachment["mime_type"], len(data)) else None,
      ))
    return result

  async def for_agent_stored(
    self, user_id: ObjectId, message_ids: list[ObjectId]
  ) -> dict[ObjectId, list[AgentAttachment]]:
    """Los adjuntos ya guardados de un lote de mensajes (el contexto), para el agente:
    solo el nombre, el contenido va únicamente con el correo nuevo."""
    grouped: dict[ObjectId, list[AgentAttachment]] = {}
    for message_id, attachments in (await self.by_message_id(user_id, message_ids)).items():
      for attachment in attachments:
        grouped.setdefault(message_id, []).append(
          AgentAttachment(filename=attachment.filename, mime_type=attachment.mime_type)
        )
    return grouped

  async def store_for_message(
    self, integration: Integration, message: Message, raw_message: dict
  ) -> list[Attachment]:
    """Baja los adjuntos del proveedor, los sube al bucket y guarda el metadato.
    Un adjunto que falle no puede tumbar el correo entero: se registra y se sigue."""
    token, attachments = await self._resolve(integration, message.provider_id, raw_message)

    stored = []
    for attachment in attachments:
      try:
        data = await self._bytes(integration, token, message.provider_id, attachment)
        key = _key(message, attachment)
        await self.storage.put(key, data, attachment["mime_type"])
        stored.append(
          await self.repository.upsert(
            Attachment(
              user_id=message.user_id,
              message_id=message.id,
              integration_id=message.integration_id,
              provider_id=message.provider_id,
              attachment_id=attachment["attachment_id"],
              filename=attachment["filename"],
              mime_type=attachment["mime_type"],
              size=attachment.get("size") or len(data),
              storage_key=key,
            )
          )
        )
        logfire.info(
          "Attachment {filename} stored for message {provider_id}",
          filename=attachment["filename"],
          provider_id=message.provider_id,
        )
      except Exception as error:
        logfire.warning(
          "Could not store attachment {filename} of {provider_id}: {error}",
          filename=attachment.get("filename"),
          provider_id=message.provider_id,
          error=error,
        )
    return stored

  async def get(self, attachment_id: ObjectId, user_id: ObjectId) -> Optional[Attachment]:
    return await self.repository.get(attachment_id, user_id)

  async def by_message_id(
    self, user_id: ObjectId, message_ids: list[ObjectId]
  ) -> dict[ObjectId, list[Attachment]]:
    """Los adjuntos de un lote de mensajes, agrupados: una consulta para todo el hilo."""
    grouped: dict[ObjectId, list[Attachment]] = {}
    for attachment in await self.repository.list_by_messages(user_id, message_ids):
      grouped.setdefault(attachment.message_id, []).append(attachment)
    return grouped

  async def download_url(self, attachment: Attachment) -> str:
    return await self.storage.signed_url(attachment.storage_key, attachment.filename)

  async def delete_by_messages(self, user_id: ObjectId, message_ids: list[ObjectId]) -> int:
    """El bucket no se limpia solo: sin este paso quedan bytes que nadie referencia."""
    keys = await self.repository.delete_by_messages(user_id, message_ids)
    await self.storage.delete(keys)
    return len(keys)

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    keys = await self.repository.delete_all_by_user(user_id)
    await self.storage.delete(keys)
    return len(keys)
