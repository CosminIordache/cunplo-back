import logfire


async def renew_watches(ctx) -> None:
  """Cron diario: renueva el push de todas las cuentas antes de que caduque.
  Sin esto, un buzón sin correo durante unos días pierde el push en silencio
  (Gmail caduca a los 7 días) y solo se recupera reconectando."""
  with logfire.span("renew_watches") as span:
    gmail = await ctx["gmail_service"].renew_expiring_watches()
    span.set_attribute("gmail", gmail)
    logfire.info("Renewed {gmail} Gmail watches", gmail=gmail)
