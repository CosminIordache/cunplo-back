import logging

from src.application.use_cases.agent_service import AgentEmailMessage
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
        logging.info("Contact %s created for user %s", extracted.email, user_id)
      except ContactEmailAlreadyUsed:
        # carrera con otro job del mismo hilo: el que perdió vuelve a leerlo
        contact = await ctx["contact_service"].get_by_email(user_id, extracted.email)
    contact_ids.append(contact.id)
  return contact_ids


async def process_gmail_notification(ctx, email: str, history_id: str) -> None:
  """El job que el worker desencola: analiza los correos nuevos y guarda los que son tarea."""
  messages = await ctx["gmail_service"].process_notification(
    email=email, history_id=history_id
  )
  if not messages:
    return

  integration = await ctx["integration_service"].get_by_email(Provider.GOOGLE, email)
  user_id = integration.user_id

  for message in messages:
    thread_id = message["thread_id"]
    stored = await ctx["message_service"].list_by_thread_id_user_id(user_id, thread_id)

    extracted = await ctx["agent_service"].run_tasks(
      owner_email=email,
      thread_messages=[_stored_to_agent_message(m) for m in stored] or None,
      new_message=_to_agent_message(message),
    )

    if not extracted:
      logging.info("Message %s for %s carries no task, skipped", message["id"], email)
      continue

    await ctx["message_service"].upsert(
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
    logging.info("Message %s saved for %s (user %s)", message["id"], email, user_id)

    await ctx["task_service"].upsert(
      Task(
        user_id=user_id,
        thread_id=thread_id,
        title=extracted.title,
        status=extracted.status,
        due_at=extracted.due_at,

        contact_ids=await _resolve_contacts(ctx, user_id, email, extracted.contacts),
      )
    )
    logging.info("Task upserted for thread %s (user %s)", thread_id, user_id)
