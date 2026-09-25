from typing import Optional

import logfire
from bson import ObjectId

from src.domain.integration import Integration, Provider
from src.application.ports.integration_repository import IntegrationRepository
from src.infrastructure.external_services import gowa

__all__ = ["WhatsAppService", "GowaError", "device_owner"]

GowaError = gowa.GowaError


def device_owner(device_id: str) -> Optional[ObjectId]:
  """El usuario dueño de un device: su id va delante ('<user_id>_<id>'). Así el device
  pendiente no necesita fila en Mongo hasta que se vincula el número."""
  user_id = device_id.partition("_")[0]
  return ObjectId(user_id) if ObjectId.is_valid(user_id) else None


class WhatsAppService:

  def __init__(self, repository: IntegrationRepository, webhook_url: str, secret: str):
    self.repository = repository
    # cada device lleva su webhook: GOWA no necesita configuración y solo nos manda lo nuestro
    self.webhook_url = webhook_url
    self.secret = secret

  async def _pending_device(self, user_id: ObjectId) -> str:
    """El device del usuario que aún no tiene número, o uno nuevo. Reutilizarlo evita
    dejar un device huérfano en GOWA por cada QR que se abre y no se escanea."""
    for device in await gowa.list_devices():
      if device_owner(device.get("id") or "") == user_id and not device.get("jid"):
        return device["id"]

    # sin webhook el número se vincularía pero nunca llegaría un mensaje, y en silencio
    if not (self.webhook_url and self.secret):
      raise GowaError("WHATSAPP_WEBHOOK_URL and GOWA_WEBHOOK_SECRET must be set")
    device_id = f"{user_id}_{ObjectId()}"
    # ponytail: la URL queda guardada en el device; si cambia el dominio de la API, los ya
    # creados siguen en la vieja: recorrerlos con PATCH /devices/{id}/webhook
    await gowa.add_device(device_id, self.webhook_url, self.secret)
    return device_id

  async def link_qr(self, user_id: ObjectId) -> tuple[str, str, int]:
    device_id = await self._pending_device(user_id)
    qr, seconds = await gowa.login_qr(device_id)
    return device_id, qr, seconds

  async def link_code(self, user_id: ObjectId, phone: str) -> tuple[str, str]:
    device_id = await self._pending_device(user_id)
    return device_id, await gowa.login_code(device_id, phone)

  async def status(self, user_id: ObjectId, device_id: str) -> Optional[Integration]:
    """La integración si el device ya tiene número vinculado; None mientras no."""
    if device_owner(device_id) != user_id:
      return None  # el device es de otro usuario: para este no existe
    device = await gowa.device(device_id)
    # el jid solo existe tras escanear el QR o meter el código
    phone = gowa.jid_to_phone(device.get("jid") or "")
    return await self.activate(user_id, device_id, phone) if phone else None

  async def activate(self, user_id: ObjectId, device_id: str, phone: str) -> Integration:
    """Crea la integración de un device recién vinculado. Idempotente: /status la
    llama en cada consulta una vez vinculado."""
    existing = await self.repository.get_by_user_account(user_id, Provider.WHATSAPP, device_id)
    if existing:
      return existing
    integration = await self.repository.upsert(
      Integration(
        user_id=user_id,
        provider=Provider.WHATSAPP,
        account_id=device_id,
        email=phone,
        scopes=[],
        refresh_token=None,
      )
    )
    logfire.info("WhatsApp {phone} linked for user {user_id}", phone=phone, user_id=user_id)
    return integration

  async def disconnect(self, integration: Integration) -> bool:
    """Cierra la sesión en WhatsApp y borra el device en GOWA antes de borrar la fila:
    si no, GOWA seguiría recibiendo los mensajes de ese número."""
    try:
      await gowa.remove_device(integration.account_id)
    except GowaError as error:
      # GOWA caído o el device ya no existe: borramos igual, como Gmail y Outlook
      logfire.warning(
        "Could not remove the GOWA device for {phone}: {error}",
        phone=integration.email,
        error=error,
      )
    return await self.repository.delete(integration.id, integration.user_id)
