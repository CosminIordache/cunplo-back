import logfire
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

  async def renew_expiring_watches(self) -> int:
    """Renueva todos los watch que caducan pronto. Lo llama el cron diario: sin él,
    un buzón sin correo durante 7 días pierde el push y nadie se entera."""
    if not self.topic:
      return 0

    # margen de 2 días: el cron es diario, así que hay una segunda oportunidad
    deadline = datetime.now(UTC) + timedelta(days=2)
    expiring = await self.repository.list_expiring(Provider.GOOGLE, deadline)

    renewed = 0
    for integration in expiring:
      try:
        # watch() es idempotente: sirve igual para renovar que para recrear
        await self.start_watch(integration)
        renewed += 1
      except (ReauthRequired, gmail.GmailError) as error:
        # una cuenta rota no puede parar las demás
        logfire.warning("Could not renew the Gmail watch for {email}: {error}", email=integration.email, error=error)

    logfire.info("Gmail watches renewed: {renewed}/{total}", renewed=renewed, total=len(expiring))
    return renewed

  async def disconnect(self, integration: Integration) -> bool:
    """Corta el push y revoca el acceso antes de borrar: si no, Google sigue publicando.
    Recibe la integración ya resuelta: el usuario puede tener varias cuentas."""
    try:
      await gmail.stop_watch(await self.integrations.access_token_for(integration))
    except (ReauthRequired, gmail.GmailError) as error:
      logfire.warning("Could not stop the Gmail watch for {email}: {error}", email=integration.email, error=error)

    if token := integration.refresh_token:
      await google_oauth.revoke_token(token)
    else:
      logfire.warning("No refresh token for {email}: access not revoked at Google", email=integration.email)

    return await self.repository.delete(integration.id, integration.user_id)

  async def process_notification(self, email: str, history_id: str) -> list[dict]:
    """Lee los mensajes nuevos que anuncia la notificación de Pub/Sub."""
    integration = await self.repository.get_by_email(Provider.GOOGLE, email)
    if not integration:
      logfire.warning("Notification for {email}: account not connected, ignored", email=email)
      return []  # cuenta desconectada: la notificación llega igual un rato

    # el watch lo renueva el cron diario, aquí solo leemos
    token = await self.integrations.access_token_for(integration)
    start = integration.history_id
    if not start:
      # nunca sincronizada: empieza a seguir desde aquí, sin traer el pasado
      integration.history_id = history_id
      await self.repository.upsert(integration)
      logfire.info("First sync for {email} from historyId {history_id}", email=email, history_id=history_id)
      return []

    try:
      ids, marker = await gmail.new_message_ids(token, start)
    except gmail.HistoryTooOld:
      # ponytail: nos saltamos el hueco; resincroniza el buzón si hace falta el pasado
      integration.history_id = await gmail.current_history_id(token)
      await self.repository.upsert(integration)
      return []

    messages = []
    for message_id in ids:
      try:
        message = await gmail.get_message(token, message_id)
      except gmail.MessageNotFound:
        # borrado o movido a spam desde que el historial lo anunció: no corta el resto
        logfire.info("Message {message_id} no longer in {email}, skipped", message_id=message_id, email=email)
        continue
      # el borrador se indexa mientras se escribe y desaparece al enviar, con otro id:
      # guardarlo deja un mensaje fantasma duplicado en el hilo
      if "DRAFT" in message["labels"]:
        logfire.info("Message {message_id} is a draft, skipped", message_id=message_id)
        continue
      messages.append(message)
      logfire.info(
        "New mail from {sender} for {email} (user {user_id})",
        sender=message.get("sender"),
        email=email,
        user_id=integration.user_id,
      )

    integration.history_id = marker
    await self.repository.upsert(integration)
    return messages
