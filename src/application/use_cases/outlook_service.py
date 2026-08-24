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
    """Activa o renueva el push de Graph. Hay que renovarlo antes de los ~3 días."""
    token = await self.integrations.access_token_for(integration)
    result = None
    if integration.subscription_id:
      try:
        result = await outlook.renew_subscription(
          token, integration.subscription_id, _expiration()
        )
      except outlook.OutlookError as error:
        # Graph no renueva una subscription ya caducada: hay que darla de alta otra vez
        logfire.warning(
          "Could not renew the Graph subscription for {email}, recreating: {error}",
          email=integration.email,
          error=error,
        )
    if result is None:
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

  async def renew_expiring_subscriptions(self) -> int:
    """Renueva todas las subscriptions que caducan pronto. Lo llama el cron diario:
    sin él, un buzón sin correo durante 3 días pierde el push y nadie se entera."""
    if not self.notification_url:
      return 0

    # margen de 1 día: Graph solo da ~3, así que el cron diario va justo
    deadline = datetime.now(UTC) + timedelta(days=1)
    expiring = await self.repository.list_expiring(Provider.MICROSOFT, deadline)

    renewed = 0
    for integration in expiring:
      try:
        await self.start_subscription(integration)
        renewed += 1
      except (ReauthRequired, outlook.OutlookError) as error:
        # una cuenta rota no puede parar las demás
        logfire.warning(
          "Could not renew the Graph subscription for {email}: {error}",
          email=integration.email,
          error=error,
        )

    logfire.info(
      "Graph subscriptions renewed: {renewed}/{total}", renewed=renewed, total=len(expiring)
    )
    return renewed

  async def sync(self, integration: Integration) -> list[dict]:
    """Correos nuevos desde la última sincronización de una cuenta. Avanza el marcador."""
    
    token = await self.integrations.access_token_for(integration)

    # primera vez: solo dejamos el marcador puesto, sin traer el buzón enter
    if not integration.history_id:
      integration.history_id = await outlook.current_delta_link(token)
      await self.repository.upsert(integration)
      logfire.info("First Outlook sync for {email}", email=integration.email)
      return []

    try:
      messages, marker = await outlook.new_messages(token, integration.history_id)
    except outlook.DeltaTooOld:
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

  async def disconnect(self, integration: Integration) -> bool:
    """Corta el push antes de borrar: si no, Graph sigue notificando por una cuenta
    que ya no existe. Microsoft no expone revocación por token: el consentimiento
    lo retira el usuario desde su cuenta."""
    if integration.subscription_id:
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
    return await self.repository.delete(integration.id, integration.user_id)
