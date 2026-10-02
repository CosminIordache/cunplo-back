from datetime import datetime
from email.utils import getaddresses, parseaddr

import logfire
from arq import Retry

from src.application.use_cases.agent_service import AgentAttachment, AgentEmailMessage
from src.infrastructure.driven.redis.functions.events import ERROR, NOT_CONTACT, PROCESSED, PROCESSING, SKIPPED, publish
from src.infrastructure.driven.redis.functions.contacts_filter import is_known_contact, resolve_contacts
from src.infrastructure.driven.redis.functions.mailbox_lock import mailbox_lock
from src.infrastructure.external_services.gmail import GmailRateLimited
from src.domain.integration import Provider
from src.domain.message import Message
from src.domain.user import ContactsFilter
from src.domain.task import Task
from src.application.use_cases.task_service import existing_task

# correos previos con las mismas personas que ve el agente
CONTEXT_MESSAGES = 20


def _addresses(message: dict, owner: str) -> list[str]:
  """Todas las personas del correo menos el dueño: el contexto se busca por ellas."""
  everyone = getaddresses([message["sender"], message["to"] or "", message["cc"] or ""])
  return sorted({a.lower() for _, a in everyone if "@" in a and a.lower() != owner.lower()})


def _sent_at(internal_date: int) -> datetime:
  return datetime.fromtimestamp(internal_date / 1000).astimezone()


def _counterparts(message: dict, owner: str) -> list[str]:
  """Con quién es el correo: el remitente, o los destinatarios si lo envía el dueño."""
  if parseaddr(message["sender"])[1].lower() != owner.lower():
    return [message["sender"]]
  return [address for _, address in getaddresses([message["to"], message["cc"] or ""]) if address]


async def _with_contact(ctx, user_id, message: dict, owner: str) -> bool:
  for address in _counterparts(message, owner):
    if await is_known_contact(ctx, user_id, address):
      return True
  return False


def _to_agent_message(message: dict, attachments: list[AgentAttachment]) -> AgentEmailMessage:
  """El correo tal y como lo devuelve Gmail: dict, no dominio."""
  return AgentEmailMessage(
    thread_id=message["thread_id"],
    sender=message["sender"],
    to=message["to"],
    cc=message["cc"] or None,
    subject=message["subject"],
    body=message["body"],
    sent_at=_sent_at(message["internal_date"]),
    attachments=attachments,
  )


def _stored_to_agent_message(message: Message, attachments: list[AgentAttachment]) -> AgentEmailMessage:
  return AgentEmailMessage(
    thread_id=message.thread_id,
    sender=message.sender,
    to=message.to,
    cc=message.cc,
    subject=message.subject,
    body=message.body,
    sent_at=_sent_at(message.internal_date),
    attachments=attachments,
  )


async def process_gmail_notification(ctx, email: str, history_id: str) -> None:
  """El job que el worker desencola: analiza y guarda los correos nuevos.
  Uno por buzón a la vez (mailbox_lock); si Gmail corta por cuota, se reintenta en un minuto."""
  async with mailbox_lock(ctx, f"gmail:{email}"):
    try:
      await _process_notification(ctx, email, history_id)
    except GmailRateLimited:
      # el history_id no avanzó: el reintento recoge los mismos correos
      logfire.warning("Gmail quota exceeded for {email}, retrying in 60s", email=email)
      raise Retry(defer=60)


async def _process_notification(ctx, email: str, history_id: str) -> None:
  with logfire.span(
    "process_gmail_notification {email}", email=email, history_id=history_id
  ) as span:
    messages = await ctx["gmail_service"].process_notification(
      email=email, history_id=history_id
    )
    span.set_attribute("messages", len(messages or []))
    if not messages:
      logfire.info("No new messages for {email}", email=email)
      return

    integration = await ctx["integration_service"].get_by_email(Provider.GOOGLE, email)
    user_id = integration.user_id

    await _process_messages(ctx, messages, email, user_id, integration)


async def _process_messages(ctx, messages, email, user_id, integration) -> None:
  # el 402 de la API no basta: aquí es donde se gasta (una llamada al LLM por correo).
  # Va después del process_notification a propósito: el history_id ya avanzó, así que
  # al reactivar no se reprocesa lo de mientras estuvo caducado
  subscription = await ctx["subscription_service"].get_by_user(user_id)
  if not (subscription and subscription.is_active):
    logfire.info("User {user_id} has no active subscription, skipped", user_id=user_id)
    return

  user = await ctx["user_service"].get(user_id)
  only_contacts = bool(user and user.only_contacts in (ContactsFilter.ALL, ContactsFilter.EMAIL))

  for message in messages:
    thread_id = message["thread_id"]
    logfire.info("Processing message {message_id} for {email} (user {user_id})", message_id=message["id"], email=email, user_id=user_id)
    event = {"integration_id": integration.id, "provider": integration.provider, "thread_id": thread_id, "message_id": message["id"], "subject": message["subject"]}
    await publish(ctx, user_id, PROCESSING, **event)
    try:

      # en un correo propio cuentan los destinatarios: el dueño no es contacto suyo
      if only_contacts and not await _with_contact(ctx, user_id, message, email):
        logfire.info("Message {message_id} from a non-contact, skipped", message_id=message["id"], email=email)
        await publish(ctx, user_id, SKIPPED, reason=NOT_CONTACT, **event)
        continue

      # el hilo no es fiable en el correo: el contexto es lo último hablado con estas
      # personas en cualquier hilo, y las tareas, las de todos esos hilos
      stored = await ctx["message_service"].list_context(
        user_id, integration.id, thread_id, _addresses(message, email), message["id"], CONTEXT_MESSAGES
      )
      related_tasks = await ctx["task_service"].get_by_threads(
        user_id, integration.id, list({thread_id, *(m.thread_id for m in stored)})
      )

      # los adjuntos del correo nuevo se bajan antes del análisis: el agente los necesita
      # para decidir. store_for_message reutiliza los bytes
      attachments = await ctx["attachment_service"].for_agent(integration, message["id"], message)
      context_attachments = await ctx["attachment_service"].for_agent_stored(user_id, [m.id for m in stored])

      extracted = await ctx["agent_service"].run_tasks(
        user_id=user_id,
        owner_email=email,
        task_language=user.task_language,
        context=[_stored_to_agent_message(m, context_attachments.get(m.id, [])) for m in stored],
        new_message=_to_agent_message(message, attachments),
        tasks=related_tasks,
      )

      # todo correo que pasa el filtro de contactos se guarda, sea tarea o no: es el
      # contexto de lo próximo que se hable con esas personas.
      # ponytail: sin only_contacts también se guardan newsletters; filtrar si pesa
      stored_message = await ctx["message_service"].upsert(
        Message(
          user_id=user_id,
          integration_id=integration.id,
          provider_id=message["id"],
          thread_id=thread_id,
          sender=message["sender"],
          to=message["to"],
          cc=message["cc"] or None,
          subject=message["subject"],
          body=message["body"],
          internal_date=message["internal_date"],
        )
      )
      logfire.info("Message {message_id} saved for {email} (user {user_id})", message_id=message["id"], email=email, user_id=user_id)

      await ctx["attachment_service"].store_for_message(integration, stored_message, message)

      # el agente dice cuál actualiza (task_id) y cuál es nueva. Una tarea de otro hilo
      # conserva el suyo: es donde nació, y el upsert filtra por él
      for item in extracted:
        existing = existing_task(item.task_id, related_tasks)
        if item.task_id and not existing:
          logfire.warning(
            "Agent returned unknown task {task_id} for thread {thread_id}, created as new",
            task_id=item.task_id,
            thread_id=thread_id,
          )
        task = Task(
          user_id=user_id,
          integration_id=integration.id,
          thread_id=existing.thread_id if existing else thread_id,
          title=item.title,
          status=item.status,
          due_at=item.due_at,
          contact_ids=await resolve_contacts(ctx, user_id, email, item.contacts),
        )
        if existing:
          task.id = existing.id
        await ctx["task_service"].upsert(task)
        logfire.info(
          "Task {task_id} {action} for thread {thread_id} (user {user_id})",
          task_id=task.id,
          action="updated" if existing else "created",
          thread_id=task.thread_id,
          user_id=user_id,
        )

      await publish(ctx, user_id, PROCESSED, tasks=len(extracted), **event)
    except Exception:
      # avisa al frontend antes de que el job caiga: si no, el aviso gira hasta caducar
      await publish(ctx, user_id, ERROR, **event)
      raise
