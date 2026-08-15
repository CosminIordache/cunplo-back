import logging
from datetime import datetime, timedelta, UTC

from bson import ObjectId

from src.domain.integration import Integration, Provider
from src.application.ports.integration_repository import IntegrationRepository
from src.application.use_cases.integration_service import IntegrationService, ReauthRequired
from src.infrastructure.external_services import gmail, google_oauth


class GmailService:
  def __init__(
    self,
    repository: IntegrationRepository,
    integrations: IntegrationService,
    topic: str,
  ):
    self.repository = repository
    self.integrations = integrations
    self.topic = topic

  async def start_watch(self, integration: Integration) -> Integration:
    """Activa el push de Gmail. Hay que renovarlo antes de los 7 días."""
    token = await self.integrations.access_token_for(integration)
    result = await gmail.watch(token, self.topic)
    integration.history_id = str(result["historyId"])
    integration.watch_expires_at = datetime.fromtimestamp(
      int(result["expiration"]) / 1000, UTC
    )
    return await self.repository.upsert(integration)

  #TODO: Esta funcion se ejecuta en el push notifications del usuario.
  #      En el futuro crear un cron diario para renovación automatica.
  async def renew_watch_if_expiring(self, integration: Integration) -> Integration:
    """Renueva el watch si le queda menos de un día. watch() es idempotente:
    volver a llamarlo solo extiende la caducidad.
    """
    if not self.topic or not integration.watch_expires_at:
      return integration

    expires_at = integration.watch_expires_at
    if expires_at.tzinfo is None:
      expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at - datetime.now(UTC) > timedelta(days=1):
      return integration

    try:
      integration = await self.start_watch(integration)
      logging.info("Gmail watch renewed for %s until %s", integration.email, integration.watch_expires_at)
    except (ReauthRequired, gmail.GmailError) as error:
      logging.warning("Could not renew the Gmail watch for %s: %s", integration.email, error)
    return integration

  async def disconnect(self, user_id: ObjectId, provider: Provider = Provider.GOOGLE) -> bool:
    """Corta el push y revoca el acceso antes de borrar: si no, Google sigue publicando.
    Una integración por usuario y provider, así que no hace falta el id."""
    integration = await self.repository.get_by_user(user_id, provider)
    if not integration:
      return False

    try:
      await gmail.stop_watch(await self.integrations.access_token_for(integration))
    except (ReauthRequired, gmail.GmailError) as error:
      logging.warning("Could not stop the Gmail watch for %s: %s", integration.email, error)

    if token := integration.refresh_token:
      await google_oauth.revoke_token(token)
    else:
      logging.warning("No refresh token for %s: access not revoked at Google", integration.email)

    return await self.repository.delete(integration.id, user_id)

  async def process_notification(self, email: str, history_id: str) -> list[dict]:
    """Lee los mensajes nuevos que anuncia la notificación de Pub/Sub."""
    integration = await self.repository.get_by_email(Provider.GOOGLE, email)
    if not integration:
      logging.warning("Notification for %s: account not connected, ignored", email)
      return []  # cuenta desconectada: la notificación llega igual un rato

    integration = await self.renew_watch_if_expiring(integration)
    token = await self.integrations.access_token_for(integration)
    start = integration.history_id
    if not start:
      # nunca sincronizada: empieza a seguir desde aquí, sin traer el pasado
      integration.history_id = history_id
      await self.repository.upsert(integration)
      logging.info("First sync for %s from historyId %s", email, history_id)
      return []

    try:
      ids, marker = await gmail.new_message_ids(token, start)
    except gmail.HistoryTooOld:
      # ponytail: nos saltamos el hueco; resincroniza el buzón si hace falta el pasado
      integration.history_id = await gmail.current_history_id(token)
      await self.repository.upsert(integration)
      return []

    messages = [await gmail.get_message(token, message_id) for message_id in ids]
    for message in messages:
      logging.info("New mail for %s (user %s) | %s", email, integration.user_id, message)

    integration.history_id = marker
    await self.repository.upsert(integration)
    return messages
