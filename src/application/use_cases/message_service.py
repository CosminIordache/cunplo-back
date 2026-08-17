from bson import ObjectId

from src.domain.message import Message
from src.application.ports.message_repository import MessageRepository


class MessageService:
  def __init__(self, repository: MessageRepository):
    self.repository = repository

  async def upsert(self, message: Message) -> Message:
    return await self.repository.upsert(message)

  async def list_by_thread_id_user_id(self, user_id: ObjectId, thread_id: str) -> list[Message]:
    return await self.repository.list_by_thread_id_user_id(user_id, thread_id)

  async def delete(self, message_id: ObjectId, user_id: ObjectId) -> bool:
    return await self.repository.delete(message_id, user_id)

  async def delete_by_thread(self, user_id: ObjectId, thread_id: str) -> int:
    return await self.repository.delete_by_thread(user_id, thread_id)
