import base64
import logfire
from typing import Optional
from bson import ObjectId

from src.domain.attachment import Attachment
from src.domain.message import Message
from src.application.ports.attachment_repository import AttachmentRepository
from src.application.ports.storage import Storage
from src.application.use_cases.integration_service import ReauthRequired
from src.domain.integration import Integration, Provider
from src.application.use_cases.integration_service import IntegrationService
from src.infrastructure.external_services import gmail, outlook


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
    """Graph ya manda los bytes en el listado; Gmail obliga a una llamada por fichero."""
    if content := attachment.get("content_bytes"):
      return base64.b64decode(content)
    if integration.provider == Provider.MICROSOFT:
      return await outlook.get_attachment(token, provider_id, attachment["attachment_id"])
    return await gmail.get_attachment(token, provider_id, attachment["attachment_id"])

  async def store_for_message(
    self, integration: Integration, message: Message, raw_message: dict
  ) -> list[Attachment]:
    """Baja los adjuntos del proveedor, los sube al bucket y guarda el metadato.
    Un adjunto que falle no puede tumbar el correo entero: se registra y se sigue."""
    attachments = raw_message.get("attachments") or []
    # Gmail los trae en el propio mensaje; el delta de Graph solo dice si los hay
    needs_listing = not attachments and raw_message.get("has_attachments")
    if not attachments and not needs_listing:
      return []

    try:
      token = await self.integrations.access_token_for(integration)
    except ReauthRequired:
      logfire.warning(
        "No token for {email}: attachments of {provider_id} not stored",
        email=integration.email,
        provider_id=message.provider_id,
      )
      return []

    if needs_listing:
      try:
        attachments = await outlook.list_attachments(token, message.provider_id)
      except outlook.OutlookError as error:
        logfire.warning(
          "Could not list attachments of {provider_id}: {error}",
          provider_id=message.provider_id,
          error=error,
        )
        return []

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
              size=attachment["size"],
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
