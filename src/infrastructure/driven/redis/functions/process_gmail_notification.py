from email.utils import getaddresses, parseaddr

import logfire
from arq import Retry

from src.application.use_cases.agent_service import AgentAttachment, AgentEmailMessage
from src.infrastructure.driven.redis.functions.events import ERROR, NO_TASK, NOT_CONTACT, PROCESSED, PROCESSING, SKIPPED, publish
from src.infrastructure.driven.redis.functions.contacts_filter import is_known_contact, resolve_contacts
from src.infrastructure.driven.redis.functions.mailbox_lock import mailbox_lock
from src.infrastructure.external_services.gmail import GmailRateLimited
from src.domain.integration import Provider
from src.domain.message import Message
from src.domain.user import ContactsFilter
from src.domain.task import Task
from src.application.use_cases.task_service import existing_task_id


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
    attachments=attachments,
  )


async def process_gmail_notification(ctx, email: str, history_id: str) -> None:
  """El job que el worker desencola: analiza los correos nuevos y guarda los que son tarea.
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

      stored = await ctx["message_service"].list_by_thread_id_user_id(
        user_id, integration.id, thread_id
      )

      thread_tasks = await ctx["task_service"].get_by_thread(
        user_id, integration.id, thread_id
      )

      # los adjuntos del correo nuevo se bajan antes de saber si se guarda: el agente los
      # necesita para decidir. store_for_message reutiliza los bytes
      attachments = await ctx["attachment_service"].for_agent(integration, message["id"], message)
      context_attachments = await ctx["attachment_service"].for_agent_stored(user_id, [m.id for m in stored])

      extracted = await ctx["agent_service"].run_tasks(
        user_id=user_id,
        owner_email=email,
        task_language=user.task_language,
        thread_messages=[_stored_to_agent_message(m, context_attachments.get(m.id, [])) for m in stored] or None,
        new_messages=[_to_agent_message(message, attachments)],
        thread_tasks=thread_tasks,
      )

      # el agente solo filtra el correo que abriría un hilo (spam, newsletters…): un hilo
      # que ya tiene tareas guarda todos sus correos, sean tarea o no, como su contexto
      if not extracted and not thread_tasks:
        logfire.info("Message {message_id} for {email} carries no task, skipped", message_id=message["id"], email=email)
        await publish(ctx, user_id, SKIPPED, reason=NO_TASK, **event)
        continue

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

      # solo los correos que se guardan llevan sus adjuntos al bucket
      await ctx["attachment_service"].store_for_message(integration, stored_message, message)

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
          user_id=user_id,
          integration_id=integration.id,
          thread_id=thread_id,
          title=item.title,
          status=item.status,
          due_at=item.due_at,
          contact_ids=await resolve_contacts(ctx, user_id, email, item.contacts),
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

      await publish(ctx, user_id, PROCESSED, tasks=len(extracted), **event)
    except Exception:
      # avisa al frontend antes de que el job caiga: si no, el aviso gira hasta caducar
      await publish(ctx, user_id, ERROR, **event)
      raise
