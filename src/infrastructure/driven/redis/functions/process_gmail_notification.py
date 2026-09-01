import logfire

from src.application.use_cases.agent_service import AgentEmailMessage
from src.infrastructure.driven.redis.functions.contacts_filter import is_known_contact
from src.application.use_cases.contact_service import ContactEmailAlreadyUsed
from src.domain.contact import Contact
from src.domain.integration import Provider
from src.domain.message import Message
from src.domain.task import Task


def _to_agent_message(message: dict) -> AgentEmailMessage:
  """El correo tal y como lo devuelve Gmail: dict, no dominio."""
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


async def _resolve_contacts(ctx, user_id, own_email: str, extracted_contacts) -> list:
  """Los contactos del agente a ids: crea los que no existen todavía."""
  contact_ids = []
  for extracted in extracted_contacts:
    # el usuario no es contacto de sí mismo, lo diga el agente o no
    if extracted.email.lower() == own_email.lower():
      continue

    contact = await ctx["contact_service"].get_by_email(user_id, extracted.email)
    if not contact:
      try:
        contact = await ctx["contact_service"].create(
          Contact(
            user_id=user_id,
            email=extracted.email,
            name=extracted.name,
            phone=extracted.phone,
          )
        )
        logfire.info("Contact {email} created for user {user_id}", email=extracted.email, user_id=user_id)
      except ContactEmailAlreadyUsed:
        # carrera con otro job del mismo hilo: el que perdió vuelve a leerlo
        contact = await ctx["contact_service"].get_by_email(user_id, extracted.email)
    contact_ids.append(contact.id)
  return contact_ids


async def process_gmail_notification(ctx, email: str, history_id: str) -> None:
  """El job que el worker desencola: analiza los correos nuevos y guarda los que son tarea."""
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
  user = await ctx["user_service"].get(user_id)
  only_contacts = bool(user and user.only_contacts)

  for message in messages:
    thread_id = message["thread_id"]
    logfire.info("Processing message {message_id} for {email} (user {user_id})", message_id=message["id"], email=email, user_id=user_id)

    if only_contacts and not await is_known_contact(ctx, user_id, message["sender"]):
      logfire.info("Message {message_id} from a non-contact, skipped", message_id=message["id"], email=email)
      continue

    stored = await ctx["message_service"].list_by_thread_id_user_id(
      user_id, integration.id, thread_id
    )

    extracted = await ctx["agent_service"].run_tasks(
      user_id=user_id,
      owner_email=email,
      thread_messages=[_stored_to_agent_message(m) for m in stored] or None,
      new_message=_to_agent_message(message),
    )

    if not extracted:
      logfire.info("Message {message_id} for {email} carries no task, skipped", message_id=message["id"], email=email)
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

    # solo los correos que son tarea llevan sus adjuntos al bucket
    await ctx["attachment_service"].store_for_message(integration, stored_message, message)

    await ctx["task_service"].upsert(
      Task(
        user_id=user_id,
        integration_id=integration.id,
        thread_id=thread_id,
        title=extracted.title,
        status=extracted.status,
        due_at=extracted.due_at,

        contact_ids=await _resolve_contacts(ctx, user_id, email, extracted.contacts),
      )
    )
    logfire.info("Task upserted for thread {thread_id} (user {user_id})", thread_id=thread_id, user_id=user_id)
