import logfire


async def purge_old_messages(ctx) -> None:
  """Cron diario: borra los correos (y sus adjuntos) de más de RETENTION_DAYS. Las tareas
  se quedan aunque sus correos ya no estén."""
  with logfire.span("purge_old_messages"):
    deleted = await ctx["message_service"].purge_old()
    logfire.info("Purged {deleted} old messages", deleted=deleted)
