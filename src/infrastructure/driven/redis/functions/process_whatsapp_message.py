import logfire

from bson import ObjectId

from src.application.use_cases.agent_service import AgentAttachment, AgentEmailMessage
from src.infrastructure.driven.redis.functions.events import ERROR, NO_TASK, NOT_CONTACT, PROCESSED, PROCESSING, SKIPPED, publish
from src.infrastructure.driven.redis.functions.contacts_filter import is_known_contact, resolve_contacts
from src.infrastructure.driven.redis.functions.mailbox_lock import mailbox_lock
from src.domain.integration import Provider
from src.infrastructure.external_services import gowa
from src.domain.message import Message
from src.domain.user import ContactsFilter
from src.domain.task import Task
from src.application.use_cases.task_service import existing_task_id


# silencio del chat antes de analizar: el webhook difiere el job esto
DEBOUNCE_SECONDS = 60
# mensajes previos que ve el agente, y los que se quedan en un chat sin tareas
CONTEXT_MESSAGES = 30
# sin marcador de análisis (chat nuevo, Redis vacío) cuenta como nuevo lo de la última hora
FALLBACK_MS = 3600 * 1000
# las claves de un chat callado caducan; sin ellas se vuelve al fallback
KEY_TTL_SECONDS = 30 * 24 * 3600


def last_key(integration_id: str, chat: str) -> str:
  """El último mensaje llegado al chat: lo escribe el webhook, lo lee el job."""
  return f"wa:last:{integration_id}:{chat}"


def done_key(integration_id: str, chat: str) -> str:
  """internal_date del último mensaje analizado del chat."""
  return f"wa:done:{integration_id}:{chat}"


# cómo ve el agente un adjunto: no se guarda el archivo, solo lo que pasó en el chat
MEDIA_LABELS = {"audio": "Nota de voz", "document": "Documento", "image": "Imagen", "video": "Vídeo"}


def media_key(integration_id: str, message_id: str) -> str:
  """La nota de voz ya transcrita: el job se reintenta entero si el chat tiene el lock."""
  return f"wa:media:{integration_id}:{message_id}"


async def _media_text(ctx, integration_id: str, user_id: ObjectId, phone: str, message: dict) -> str:
  """El body del mensaje con su adjunto convertido en texto. Una nota de voz se transcribe;
  del resto basta con saber que se envió (y su caption, que ya viene en body)."""
  media, body = message.get("media"), message["body"]
  if not media:
    return body
  label = MEDIA_LABELS[media["kind"]]
  if media["kind"] == "document":
    # GOWA renombra el archivo al guardarlo: de su nombre original solo queda la extensión
    label += f" {media['path'].rpartition('.')[2].upper()}"
  if media["kind"] == "audio":
    key = media_key(integration_id, message["id"])
    cached = await ctx["redis"].get(key)
    if cached is not None:
      text = cached.decode()
    else:
      try:
        audio = await gowa.media(media["path"])
        text = await ctx["transcription_service"].audio_transcription(
          audio, media["path"].rpartition("/")[2], user_id, phone
        )
      except Exception as error:
        # ponytail: sin reintento; un audio que GOWA ya no tiene no vuelve. Reintentar si
        # los fallos de OpenAI resultan ser transitorios
        logfire.warning("Voice note {message_id} not transcribed: {error}", message_id=message["id"], error=str(error))
        text = ""
      await ctx["redis"].set(key, text, ex=3600)
    label = f"{label}: {text}" if text else f"{label} (sin transcribir)"
  return f"[{label}]\n{body}" if body else f"[{label}]"


async def _publish_burst(ctx, user_id, integration_id, burst: list[Message], status: str, **data) -> None:
  """Un evento por mensaje de la ráfaga: el frontend los sigue por message_id, como en correo."""
  for m in burst:
    await publish(ctx, user_id, status, integration_id=integration_id, provider=Provider.WHATSAPP, thread_id=m.thread_id, message_id=m.provider_id, subject="", **data)


def _stored_to_agent_message(message: Message, attachments: list[AgentAttachment] | None = None) -> AgentEmailMessage:
  return AgentEmailMessage(
    thread_id=message.thread_id,
    sender=message.sender,
    to=message.to,
    subject="",
    body=message.body,
    channel="whatsapp",
    attachments=attachments or [],
  )


async def process_whatsapp_message(ctx, integration_id: str, user_id: str, message: dict) -> None:
  """El job que el worker desencola, DEBOUNCE_SECONDS después de cada mensaje. Todo
  mensaje se guarda (es el contexto del chat); solo el job del último mensaje de la
  ráfaga llama al agente, con todo lo llegado desde el último análisis. Un "hola" y la
  petición que le sigue son así una sola llamada. Lo encola el webhook de GOWA, que ya
  trae el mensaje: no hay nada que sincronizar. El resto como process_gmail_notification."""
  # ponytail: un chat que nunca calla DEBOUNCE_SECONDS no se analiza hasta la pausa;
  # añadir una espera máxima si pasa de verdad
  integration_oid, user_oid = ObjectId(integration_id), ObjectId(user_id)
  with logfire.span("process_whatsapp_message {integration_id}", integration_id=integration_id):
    integration = await ctx["integration_repository"].get(integration_oid, user_oid)
    if not integration:
      logfire.warning("Integration {integration_id} is gone, message skipped", integration_id=integration_id)
      return

    phone = integration.phone
    assert phone  # la entidad lo exige en WhatsApp
    subscription = await ctx["subscription_service"].get_by_user(user_oid)
    if not (subscription and subscription.is_active):
      logfire.info("User {user_id} has no active subscription, skipped", user_id=user_id)
      return

    user = await ctx["user_service"].get(user_oid)
    thread_id = message["thread_id"]
    logfire.info("Processing WhatsApp message {message_id} for {phone} (user {user_id})", message_id=message["id"], phone=phone, user_id=user_id)

    # en un mensaje propio el contacto que cuenta es el destinatario
    counterpart = message["to"] if message["sender"] == phone else message["sender"]
    if user and user.only_contacts in (ContactsFilter.ALL, ContactsFilter.WHATSAPP) and not await is_known_contact(ctx, user_oid, counterpart):
      logfire.info("Message {message_id} with a non-contact, skipped", message_id=message["id"])
      await publish(ctx, user_oid, SKIPPED, reason=NOT_CONTACT, integration_id=integration_id, provider=Provider.WHATSAPP, thread_id=thread_id, message_id=message["id"], subject="")
      return

    body = await _media_text(ctx, integration_id, user_oid, phone, message)
    # se guarda siempre, haya tarea o no: es el contexto del próximo análisis
    stored_message = await ctx["message_service"].upsert(
      Message(
        user_id=user_oid,
        integration_id=integration.id,
        provider_id=message["id"],
        thread_id=thread_id,
        sender=message["sender"],
        to=message["to"],
        subject="",
        body=body,
        internal_date=message["internal_date"],
      )
    )
    if message.get("media"):
      # ponytail: un reintento por lock lo vuelve a bajar y subir (mismo key, se pisa);
      # y la nota de voz se baja dos veces, para transcribir y para el bucket
      await ctx["attachment_service"].store_for_message(integration, stored_message, {"attachments": [message["media"]]})

    redis = ctx["redis"]
    last = await redis.get(last_key(integration_id, thread_id))
    if last and last.decode() != message["id"]:
      logfire.info("Message {message_id} stored, a later one will analyze the burst", message_id=message["id"])
      return

    # un análisis por chat a la vez: dos ráfagas seguidas leerían el mismo marcador
    async with mailbox_lock(ctx, f"wa:{integration_id}:{thread_id}"):
      done = await redis.get(done_key(integration_id, thread_id))
      # ponytail: sin marcador (Redis vacío) se toma la última hora; puede reanalizar,
      # pero las tareas van por task_id y no se duplican
      since = int(done) if done else message["internal_date"] - FALLBACK_MS
      stored = await ctx["message_service"].list_by_thread_id_user_id(user_oid, integration.id, thread_id)
      previous = [m for m in stored if m.internal_date <= since]
      burst = [m for m in stored if m.internal_date > since]
      if not burst:
        logfire.info("Chat {thread_id} already analyzed, skipped", thread_id=thread_id)
        return

      await _publish_burst(ctx, user_oid, integration_id, burst, PROCESSING)
      try:
        thread_tasks = await ctx["task_service"].get_by_thread(user_oid, integration.id, thread_id)
        # cada mensaje de la ráfaga ya subió su adjunto en su propio job: se lee del bucket
        burst_attachments = await ctx["attachment_service"].for_agent_stored(user_oid, [m.id for m in burst], with_data=True)
        extracted = await ctx["agent_service"].run_tasks(
          user_id=user_oid,
          owner_email=phone,
          task_language=user.task_language,
          thread_messages=[_stored_to_agent_message(m) for m in previous[-CONTEXT_MESSAGES:]] or None,
          new_messages=[_stored_to_agent_message(m, burst_attachments.get(m.id)) for m in burst],
          thread_tasks=thread_tasks,
        )
        await redis.set(done_key(integration_id, thread_id), burst[-1].internal_date, ex=KEY_TTL_SECONDS)

        if not extracted:
          logfire.info("Burst of {count} messages in chat {thread_id} carries no task", count=len(burst), thread_id=thread_id)
          # un chat sin tareas no acumula: se queda solo lo que sirve de contexto. Con tareas
          # se guarda entero y cae con la última tarea (task_service.delete)
          if not thread_tasks and len(stored) > CONTEXT_MESSAGES:
            await ctx["message_service"].delete_many(user_oid, [m.id for m in stored[:-CONTEXT_MESSAGES]])
          # mismo criterio que en correo: en un chat con tareas el mensaje queda como contexto
          if thread_tasks:
            await _publish_burst(ctx, user_oid, integration_id, burst, PROCESSED, tasks=0)
          else:
            await _publish_burst(ctx, user_oid, integration_id, burst, SKIPPED, reason=NO_TASK)
          return

        # varias tareas por hilo: el agente dice cuál actualiza (task_id) y cuál es nueva
        for item in extracted:
          task_id = existing_task_id(item.task_id, thread_tasks)
          if item.task_id and not task_id:
            logfire.warning(
              "Agent returned unknown task {task_id} for thread {thread_id}, created as new",
              task_id=item.task_id,
              thread_id=thread_id,
            )
          task = Task(
            user_id=user_oid,
            integration_id=integration.id,
            thread_id=thread_id,
            title=item.title,
            status=item.status,
            due_at=item.due_at,
            contact_ids=await resolve_contacts(ctx, user_oid, phone, item.contacts),
          )
          if task_id:
            task.id = task_id
          await ctx["task_service"].upsert(task)
          logfire.info(
            "Task {task_id} {action} for thread {thread_id} (user {user_id})",
            task_id=task.id,
            action="updated" if task_id else "created",
            thread_id=thread_id,
            user_id=user_id,
          )

        await _publish_burst(ctx, user_oid, integration_id, burst, PROCESSED, tasks=len(extracted))
      except Exception:
        # como en correo: el frontend se entera antes de que el job caiga
        await _publish_burst(ctx, user_oid, integration_id, burst, ERROR)
        raise
