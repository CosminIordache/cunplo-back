import logfire


async def renew_watches(ctx) -> None:
  """Cron diario: renueva el push de todas las cuentas antes de que caduque.
  Sin esto, un buzón sin correo durante unos días pierde el push en silencio
  (Gmail caduca a los 7 días, Graph a los ~3) y solo se recupera reconectando."""
  with logfire.span("renew_watches") as span:
    gmail = await ctx["gmail_service"].renew_expiring_watches()
    outlook = await ctx["outlook_service"].renew_expiring_subscriptions()
    span.set_attribute("gmail", gmail)
    span.set_attribute("outlook", outlook)
    logfire.info(
      "Renewed {gmail} Gmail watches and {outlook} Graph subscriptions",
      gmail=gmail,
      outlook=outlook,
    )
