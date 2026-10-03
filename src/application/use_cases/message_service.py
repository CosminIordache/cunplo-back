from datetime import datetime, timedelta, UTC

from bson import ObjectId

from src.domain.message import Message
from src.application.ports.message_repository import MessageRepository
from src.application.use_cases.attachment_service import AttachmentService


# Los correos son contexto para el agente, no un archivo: pasado este tiempo se borran
RETENTION_DAYS = 90


class MessageService:
  def __init__(self, repository: MessageRepository, attachments: AttachmentService):
    self.repository = repository
    self.attachments = attachments

  async def upsert(self, message: Message) -> Message:
    return await self.repository.upsert(message)

  async def list_by_thread_id_user_id(
    self, user_id: ObjectId, integration_id: ObjectId, thread_id: str
  ) -> list[Message]:
    return await self.repository.list_by_thread_id_user_id(user_id, integration_id, thread_id)

  async def list_context(
    self,
    user_id: ObjectId,
    integration_id: ObjectId,
    thread_id: str,
    addresses: list[str],
    exclude_provider_id: str,
    limit: int,
  ) -> list[Message]:
    return await self.repository.list_context(
      user_id, integration_id, thread_id, addresses, exclude_provider_id, limit
    )

  async def list_thread_with_attachments(
    self, user_id: ObjectId, integration_id: ObjectId, thread_id: str
  ) -> list[dict]:
    """El hilo listo para la API: cada correo con sus adjuntos, en una sola consulta
    extra. El dominio no los lleva dentro porque viven en otra colección."""
    messages = await self.list_by_thread_id_user_id(user_id, integration_id, thread_id)
    grouped = await self.attachments.by_message_id(user_id, [m.id for m in messages])
    return [{**vars(m), "attachments": grouped.get(m.id, [])} for m in messages]

  async def delete(self, message_id: ObjectId, user_id: ObjectId) -> bool:
    await self.attachments.delete_by_messages(user_id, [message_id])
    return await self.repository.delete(message_id, user_id)

  async def delete_many(self, user_id: ObjectId, message_ids: list[ObjectId]) -> int:
    await self.attachments.delete_by_messages(user_id, message_ids)
    return await self.repository.delete_many(user_id, message_ids)

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    await self.attachments.delete_all_by_user(user_id)
    return await self.repository.delete_all_by_user(user_id)

  async def delete_by_integration(self, user_id: ObjectId, integration_id: ObjectId) -> int:
    ids = await self.repository.ids_by_integration(user_id, integration_id)
    return await self.delete_many(user_id, ids)

  async def purge_old(self) -> int:
    """Por la fecha del correo (internal_date), no por cuándo se guardó."""
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    grouped = await self.repository.ids_older_than(int(cutoff.timestamp() * 1000))
    # ponytail: un usuario detrás de otro; basta mientras la purga diaria sea pequeña
    return sum([await self.delete_many(user_id, ids) for user_id, ids in grouped.items()])
