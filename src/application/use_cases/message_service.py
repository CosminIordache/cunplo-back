from bson import ObjectId

from src.domain.message import Message
from src.application.ports.message_repository import MessageRepository
from src.application.use_cases.attachment_service import AttachmentService


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

  async def delete_by_thread(
    self, user_id: ObjectId, integration_id: ObjectId, thread_id: str
  ) -> int:
    # hay que leerlos antes: los adjuntos viven en otra colección y solo saben del message_id
    messages = await self.repository.list_by_thread_id_user_id(user_id, integration_id, thread_id)
    await self.attachments.delete_by_messages(user_id, [m.id for m in messages])
    return await self.repository.delete_by_thread(user_id, integration_id, thread_id)

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    await self.attachments.delete_all_by_user(user_id)
    return await self.repository.delete_all_by_user(user_id)
