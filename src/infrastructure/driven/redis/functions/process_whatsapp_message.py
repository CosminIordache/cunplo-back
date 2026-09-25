import logfire

from bson import ObjectId

from src.application.use_cases.agent_service import AgentEmailMessage
from src.infrastructure.driven.redis.functions.contacts_filter import is_known_contact, resolve_contacts
from src.domain.message import Message
from src.domain.task import Task
from src.application.use_cases.task_service import existing_task_id


def _to_agent_message(message: dict) -> AgentEmailMessage:
  """El mensaje tal y como lo deja gowa.to_message: dict, no dominio."""
  return AgentEmailMessage(
    thread_id=message["thread_id"],
    sender=message["sender"],
    to=message["to"],
    subject="",
    body=message["body"],
    channel="whatsapp",
  )


def _stored_to_agent_message(message: Message) -> AgentEmailMessage:
  return AgentEmailMessage(
    thread_id=message.thread_id,
    sender=message.sender,
    to=message.to,
    subject="",
    body=message.body,
    channel="whatsapp",
  )


async def process_whatsapp_message(ctx, integration_id: str, user_id: str, message: dict) -> None:
  """El job que el worker desencola: analiza un mensaje de WhatsApp y lo guarda si es
  tarea. Lo encola el webhook de GOWA, que ya trae el mensaje: no hay nada que sincronizar.
  Misma lógica que process_outlook_sync; mantenerlos a la par."""
  # ponytail: una llamada al LLM por mensaje; agrupar por chat con una lista en Redis
  # + job diferido si el coste de WhatsApp pesa (llega en ráfagas de mensajes cortos)
  integration_oid, user_oid = ObjectId(integration_id), ObjectId(user_id)
  with logfire.span("process_whatsapp_message {integration_id}", integration_id=integration_id):
    integration = await ctx["integration_repository"].get(integration_oid, user_oid)
    if not integration:
      logfire.warning("Integration {integration_id} is gone, message skipped", integration_id=integration_id)
      return

    phone = integration.email
    subscription = await ctx["subscription_service"].get_by_user(user_oid)
    if not (subscription and subscription.is_active):
      logfire.info("User {user_id} has no active subscription, skipped", user_id=user_id)
      return

    user = await ctx["user_service"].get(user_oid)
    thread_id = message["thread_id"]
    logfire.info("Processing WhatsApp message {message_id} for {phone} (user {user_id})", message_id=message["id"], phone=phone, user_id=user_id)

    # en un mensaje propio el contacto que cuenta es el destinatario
    counterpart = message["to"] if message["sender"] == phone else message["sender"]
    if user and user.only_contacts and not await is_known_contact(ctx, user_oid, counterpart):
      logfire.info("Message {message_id} with a non-contact, skipped", message_id=message["id"])
      return

    stored = await ctx["message_service"].list_by_thread_id_user_id(user_oid, integration.id, thread_id)
    thread_tasks = await ctx["task_service"].get_by_thread(user_oid, integration.id, thread_id)

    extracted = await ctx["agent_service"].run_tasks(
      user_id=user_oid,
      owner_email=phone,
      task_language=user.task_language,
      thread_messages=[_stored_to_agent_message(m) for m in stored] or None,
      new_message=_to_agent_message(message),
      thread_tasks=thread_tasks,
    )
    if not extracted:
      logfire.info("Message {message_id} for {phone} carries no task, skipped", message_id=message["id"], phone=phone)
      return

    await ctx["message_service"].upsert(
      Message(
        user_id=user_oid,
        integration_id=integration.id,
        provider_id=message["id"],
        thread_id=thread_id,
        sender=message["sender"],
        to=message["to"],
        subject="",
        body=message["body"],
        internal_date=message["internal_date"],
      )
    )
    logfire.info("Message {message_id} saved for {phone} (user {user_id})", message_id=message["id"], phone=phone, user_id=user_id)

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
