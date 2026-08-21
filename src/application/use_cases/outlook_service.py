import logfire
from datetime import datetime, timedelta, UTC

from bson import ObjectId

from src.domain.integration import Integration, Provider
from src.application.ports.integration_repository import IntegrationRepository
from src.application.use_cases.integration_service import IntegrationService, ReauthRequired
from src.infrastructure.external_services import outlook

# Graph rechaza más de ~4230 minutos para correo; nos quedamos algo por debajo
SUBSCRIPTION_DAYS = 2.5


def _expiration() -> str:
  """Caducidad en el ISO-8601 con Z que exige Graph."""
  when = datetime.now(UTC) + timedelta(days=SUBSCRIPTION_DAYS)
  return when.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


class OutlookService:
  def __init__(
    self,
    repository: IntegrationRepository,
    integrations: IntegrationService,
    notification_url: str,
    secret: str,
  ):
    self.repository = repository
    self.integrations = integrations
    self.notification_url = notification_url
    self.secret = secret

  async def start_subscription(self, integration: Integration) -> Integration:
    """Activa el push de Graph. Hay que renovarlo antes de los ~3 días."""
    token = await self.integrations.access_token_for(integration)
    result = await outlook.subscribe(
      token, self.notification_url, self.secret, _expiration()
    )
    integration.subscription_id = result["id"]
    integration.watch_expires_at = datetime.fromisoformat(
      result["expirationDateTime"].replace("Z", "+00:00")
    )
    # el delta arranca aquí: lo que llegue a partir de ahora es lo que procesamos
    if not integration.history_id:
      integration.history_id = await outlook.current_delta_link(token)
    return await self.repository.upsert(integration)

  ##TODO: la renovación depende de que llegue un correo. Si la subscription caduca del
  #       todo (worker parado >3 días, o buzón sin actividad en ese tiempo) el push deja
  #       de llegar y solo se recupera reconectando la cuenta a mano. Crear un cron diario
  #       que renueve las que estén por caducar, sin esperar a la notificación.
  async def renew_subscription_if_expiring(self, integration: Integration) -> Integration:
    """Renueva si le queda menos de un día, igual que el watch de Gmail."""
    if not self.notification_url or not integration.subscription_id:
      return integration

    expires_at = integration.watch_expires_at
    if expires_at:
      if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
      if expires_at - datetime.now(UTC) > timedelta(days=1):
        return integration

    try:
      token = await self.integrations.access_token_for(integration)
      result = await outlook.renew_subscription(
        token, integration.subscription_id, _expiration()
      )
      integration.watch_expires_at = datetime.fromisoformat(
        result["expirationDateTime"].replace("Z", "+00:00")
      )
      integration = await self.repository.upsert(integration)
      logfire.info(
        "Graph subscription renewed for {email} until {expires_at}",
        email=integration.email,
        expires_at=integration.watch_expires_at,
      )
    except (ReauthRequired, outlook.OutlookError) as error:
      # caducada del todo: la próxima notificación no llegará, se recrea al reconectar
      logfire.warning(
        "Could not renew the Graph subscription for {email}: {error}",
        email=integration.email,
        error=error,
      )
    return integration

  async def sync(self, user_id: ObjectId) -> list[dict]:
    """Correos nuevos desde la última sincronización. Avanza el marcador."""
    integration = await self.repository.get_by_user(user_id, Provider.MICROSOFT)
    if not integration:
      return []

    # de paso, como el watch de Gmail: sin cron, aprovechando que ha llegado un correo
    integration = await self.renew_subscription_if_expiring(integration)
    token = await self.integrations.access_token_for(integration)

    # primera vez: solo dejamos el marcador puesto, sin traer el buzón entero
    if not integration.history_id:
      integration.history_id = await outlook.current_delta_link(token)
      await self.repository.upsert(integration)
      logfire.info("First Outlook sync for {email}", email=integration.email)
      return []

    try:
      messages, marker = await outlook.new_messages(token, integration.history_id)
    except outlook.DeltaTooOld:
      # ponytail: nos saltamos el hueco, igual que Gmail con HistoryTooOld
      integration.history_id = await outlook.current_delta_link(token)
      await self.repository.upsert(integration)
      logfire.info("Outlook delta expired for {email}, resynced", email=integration.email)
      return []

    # el borrador se indexa mientras se escribe y desaparece al enviar, con otro id
    messages = [m for m in messages if "DRAFT" not in m["labels"]]

    integration.history_id = marker
    await self.repository.upsert(integration)
    logfire.info(
      "Outlook sync for {email}: {count} new messages",
      email=integration.email,
      count=len(messages),
    )
    return messages

  async def disconnect(self, user_id: ObjectId) -> bool:
    """Corta el push antes de borrar: si no, Graph sigue notificando por una cuenta
    que ya no existe. Microsoft no expone revocación por token: el consentimiento
    lo retira el usuario desde su cuenta."""
    integration = await self.repository.get_by_user(user_id, Provider.MICROSOFT)
    if integration and integration.subscription_id:
      try:
        token = await self.integrations.access_token_for(integration)
        await outlook.unsubscribe(token, integration.subscription_id)
      except (ReauthRequired, outlook.OutlookError) as error:
        # token muerto: borramos igual, la subscription caduca sola en 3 días
        logfire.warning(
          "Could not delete the Graph subscription for {email}: {error}",
          email=integration.email,
          error=error,
        )
    if not integration:
      return False
    return await self.repository.delete(integration.id, user_id)
