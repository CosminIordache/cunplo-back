import logfire

from bson import ObjectId

from src.application.use_cases.agent_service import AgentEmailMessage
from src.application.use_cases.contact_service import ContactEmailAlreadyUsed
from src.domain.contact import Contact
from src.domain.message import Message
from src.domain.task import Task


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


async def process_outlook_sync(ctx, integration_id: str, user_id: str) -> None:
  """El job que el worker desencola: analiza los correos nuevos de una cuenta de
  Outlook y guarda los que son tarea. Lo encola el webhook de Graph."""
  # arq serializa el job: el ObjectId viaja como str y aquí se reconstruye
  integration_id, user_id = ObjectId(integration_id), ObjectId(user_id)
  with logfire.span("process_outlook_sync {integration_id}", integration_id=integration_id) as span:
    integration = await ctx["integration_repository"].get(integration_id, user_id)
    if not integration:
      logfire.warning("Integration {integration_id} is gone, sync skipped", integration_id=integration_id)
      return

    email = integration.email
    messages = await ctx["outlook_service"].sync(integration)
    span.set_attribute("messages", len(messages or []))
    if not messages:
      logfire.info("No new Outlook messages for {email}", email=email)
      return

    for message in messages:
      thread_id = message["thread_id"]
      logfire.info("Processing message {message_id} for {email} (user {user_id})", message_id=message["id"], email=email, user_id=user_id)
      stored = await ctx["message_service"].list_by_thread_id_user_id(
        user_id, integration.id, thread_id
      )

      extracted = await ctx["agent_service"].run_tasks(
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
