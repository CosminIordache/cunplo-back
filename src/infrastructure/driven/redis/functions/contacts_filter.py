from email.utils import parseaddr


async def is_known_contact(ctx, user_id, sender: str) -> bool:
  """El remitente ('Nombre <a@b.com>' o 'a@b.com') ya es contacto del usuario.
  La comparten los dos jobs: el filtro only_contacts es el mismo en Gmail y Outlook."""
  # ponytail: solo mira el remitente, no To/Cc; amplía si hace falta filtrar por destinatario
  email = parseaddr(sender)[1]
  return bool(email) and bool(await ctx["contact_service"].get_by_email(user_id, email))
