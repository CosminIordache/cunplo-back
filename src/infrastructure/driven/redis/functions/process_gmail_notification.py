import logging

from src.domain.integration import Provider
from src.domain.message import Message


async def process_gmail_notification(ctx, email: str, history_id: str) -> None:
  """El job que el worker desencola: lee los correos nuevos y los guarda."""
  messages = await ctx["gmail_service"].process_notification(
    email=email, history_id=history_id
  )
  if not messages:
    return

  integration = await ctx["integration_service"].get_by_email(Provider.GOOGLE, email)
  for message in messages:
    await ctx["message_service"].upsert(
      Message(
        user_id=integration.user_id,
        integration_id=integration.id,
        provider_id=message["id"],
        thread_id=message["thread_id"],
        sender=message["sender"],
        to=message["to"],
        cc=message["cc"] or None,
        subject=message["subject"],
        body=message["body"],
        internal_date=message["internal_date"],
      )
    )
    logging.info(
      "Message %s saved for %s (user %s)", message["id"], email, integration.user_id
    )
