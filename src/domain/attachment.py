from dataclasses import dataclass, field
from datetime import datetime, UTC

from bson import ObjectId


@dataclass
class Attachment:
  """Un adjunto de un correo. Los bytes viven en el bucket; aquí solo el metadato
  y la clave para encontrarlos."""

  user_id: ObjectId
  message_id: ObjectId  # el Message al que pertenece: sin él, el adjunto no existe
  integration_id: ObjectId
  provider_id: str  # id del mensaje en Gmail, para volver a pedirlo si hiciera falta
  attachment_id: str  # id de la parte en Gmail: único dentro del mensaje

  filename: str
  mime_type: str
  size: int  # bytes, tal y como los cuenta Gmail
  storage_key: str  # ruta dentro del bucket

  id: ObjectId = field(default_factory=ObjectId)
  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
