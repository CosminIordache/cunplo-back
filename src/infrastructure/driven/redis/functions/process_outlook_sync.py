import logfire
from arq import Retry

from bson import ObjectId

from src.application.use_cases.agent_service import AgentEmailMessage
from src.infrastructure.driven.redis.functions.contacts_filter import is_known_contact, resolve_contacts
from src.infrastructure.driven.redis.functions.mailbox_lock import mailbox_lock
from src.infrastructure.external_services.outlook import OutlookRateLimited
from src.domain.message import Message
from src.domain.task import Task
from src.application.use_cases.task_service import existing_task_id


def _to_agent_message(message: dict) -> AgentEmailMessage:
  """El correo tal y como lo devuelve Graph: dict, no dominio."""
  return AgentEmailMessage(
    thread_id=message["thread_id"],
    sender=message["sender"],
    to=message["to"],
    cc=message["cc"] or None,
    subject=message["subject"],
    body=message["body"],
  )


def _stored_to_agent_message(message: Message) -> AgentEmailMessage:
  return AgentEmailMessage(
    thread_id=message.thread_id,
    sender=message.sender,
    to=message.to,
    cc=message.cc,
    subject=message.subject,
    body=message.body,
  )


async def process_outlook_sync(ctx, integration_id: str, user_id: str) -> None:
  """El job que el worker desencola: analiza los correos nuevos de una cuenta de
  Outlook y guarda los que son tarea. Lo encola el webhook de Graph. Uno por cuenta a
  la vez (mailbox_lock); si Graph nos frena, se reintenta en un minuto."""
  async with mailbox_lock(ctx, f"outlook:{integration_id}"):
    try:
      await _sync(ctx, integration_id, user_id)
    except OutlookRateLimited:
      # el delta no avanzó: el reintento recoge los mismos correos
      logfire.warning("Graph throttled {integration_id}, retrying in 60s", integration_id=integration_id)
      raise Retry(defer=60)


async def _sync(ctx, integration_id: str, user_id: str) -> None:
  # arq serializa el job: el ObjectId viaja como str y aquí se reconstruye
  integration_oid, user_oid = ObjectId(integration_id), ObjectId(user_id)
  with logfire.span("process_outlook_sync {integration_id}", integration_id=integration_id) as span:
    integration = await ctx["integration_repository"].get(integration_oid, user_oid)
    if not integration:
      logfire.warning("Integration {integration_id} is gone, sync skipped", integration_id=integration_id)
      return

    email = integration.email
    messages = await ctx["outlook_service"].sync(integration)
    span.set_attribute("messages", len(messages or []))
    if not messages:
      logfire.info("No new Outlook messages for {email}", email=email)
      return

    # el 402 de la API no basta: aquí es donde se gasta (una llamada al LLM por
    # correo). Va después del sync a propósito: el delta ya avanzó, así que al
    # reactivar no se reprocesa lo de mientras estuvo caducado
    subscription = await ctx["subscription_service"].get_by_user(user_oid)
    if not (subscription and subscription.is_active):
      logfire.info("User {user_id} has no active subscription, skipped", user_id=user_id)
      return

    user = await ctx["user_service"].get(user_oid)
    only_contacts = bool(user and user.only_contacts)

    for message in messages:
      thread_id = message["thread_id"]
      logfire.info("Processing message {message_id} for {email} (user {user_id})", message_id=message["id"], email=email, user_id=user_id)

      if only_contacts and not await is_known_contact(ctx, user_oid, message["sender"]):
        logfire.info("Message {message_id} from a non-contact, skipped", message_id=message["id"], email=email)
        continue

      stored = await ctx["message_service"].list_by_thread_id_user_id(
        user_oid, integration.id, thread_id
      )

      thread_tasks = await ctx["task_service"].get_by_thread(
        user_oid, integration.id, thread_id
      )

      extracted = await ctx["agent_service"].run_tasks(
        user_id=user_oid,
        owner_email=email,
        task_language=user.task_language,
        thread_messages=[_stored_to_agent_message(m) for m in stored] or None,
        new_message=_to_agent_message(message),
        thread_tasks=thread_tasks,
      )

      if not extracted:
        logfire.info("Message {message_id} for {email} carries no task, skipped", message_id=message["id"], email=email)
        continue

      stored_message = await ctx["message_service"].upsert(
        Message(
          user_id=user_oid,
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

      # solo los correos que son tarea llevan sus adjuntos al bucket
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
          user_id=user_oid,
          integration_id=integration.id,
          thread_id=thread_id,
          title=item.title,
          status=item.status,
          due_at=item.due_at,
          contact_ids=await resolve_contacts(ctx, user_oid, email, item.contacts),
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
