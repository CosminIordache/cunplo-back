import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from pydantic_ai import Agent

from src.domain.task import Status

@dataclass
class ExtractedContact:
  """Contact sin ids: el LLM no puede rellenar un ObjectId."""

  email: str
  name: Optional[str] = None
  phone: Optional[str] = None


@dataclass
class ExtractedTask:
  title: str
  status: Status
  contacts: List[ExtractedContact]
  due_at: Optional[datetime] = None

@dataclass
class AgentEmailMessage:
  thread_id: str
  
  sender: str
  to: str
  subject: str
  body: str
  cc: Optional[str] = None

INSTRUCTIONS = """
Llevas el control del trabajo pendiente de un autónomo o pequeño negocio a partir de su correo.
Cada hilo es como mucho una tarea: lo que el dueño del buzón todavía debe hacer, o lo que espera.

Recibes el hilo previo como contexto y después el CORREO NUEVO. Decide sobre el CORREO NUEVO
si hay tarea; el hilo solo sirve para entender a qué se refiere.
El correo del dueño del buzón te lo dan al principio del prompt. Fíjate en él para saber de
qué lado estás: si el CORREO NUEVO sale del dueño, es él quien acaba de responder.

Campos:
- title: frase corta con la acción concreta. No es un resumen del correo.
- status TODO: le toca actuar al dueño (una petición, una pregunta, un plazo suyo).
- status WAITING_RESPONSE: el dueño ya respondió y espera a la otra parte.
- status DONE: el hilo cierra la acción (entregado, pagado, confirmado, cancelado).
- status TO_VALIDATE: hay algo pendiente pero no sabes de quién es el turno.
- contacts: las personas implicadas en la tarea, sin el dueño del buzón. Incluye a las
  que van en copia (Cc) si son personas reales. Saca name y phone de la firma del correo
  cuando estén; si no aparecen, déjalos null y nunca los inventes.
  Descarta buzones genéricos y automáticos: info@, noreply@, no-reply@, ventas@, soporte@,
  hola@, admin@, facturacion@, newsletter@ y similares. Solo personas.
- due_at: solo cuando el hilo da una fecha concreta. Nunca la inventes ni la estimes.

El estado describe cómo queda el hilo tras el correo nuevo, no cómo estaba antes. Un hilo
cerrado se reabre si el correo nuevo pide algo más: el presupuesto que enviaste lo dejó en
DONE, pero si ahora te piden cambios, la tarea vuelve a TODO con el título de lo nuevo.

Si el correo nuevo no lleva ninguna tarea, devuelve null. No te inventes una para rellenar.
Newsletters, marketing, notificaciones, recibos y un simple 'gracias' no llevan tarea.
Si dudas de quién es la acción, usa TO_VALIDATE en vez de adivinar.
"""


class AgentService:
  def __init__(self):
    self.agent = Agent(
      model= "openai:gpt-5.6-luna",
      output_type=ExtractedTask | None,
      instructions=INSTRUCTIONS
     )
    
  async def run_tasks(self, owner_email: str, thread_messages: Optional[List[AgentEmailMessage]], new_message: AgentEmailMessage) -> Optional[ExtractedTask]:
    logging.info(
      "Agent analyzing thread %s (%s previous mails) from %s for owner %s",
      new_message.thread_id,
      len(thread_messages or []),
      new_message.sender,
      owner_email,
    )
    result = await self.agent.run(self._prompt(owner_email, thread_messages, new_message))

    usage = result.usage
    if not result.output:
      logging.info(
        "Agent found no task in thread %s | %s tokens", new_message.thread_id, usage.total_tokens
      )
      return None

    task = result.output
    logging.info(
      "Agent found task in thread %s | %s | '%s' | due=%s | contacts=%s | %s tokens",
      new_message.thread_id,
      task.status,
      task.title,
      task.due_at,
      task.contacts,
      usage.total_tokens,
    )
    return task

  def _prompt(
    self,
    owner_email: str,
    thread_messages: Optional[List[AgentEmailMessage]],
    new_message: AgentEmailMessage,
  ) -> str:

    context = "\n\n---\n\n".join(self._format(m) for m in thread_messages or [])
    return (
      f"DUEÑO DEL BUZÓN: {owner_email}"
      f"\n\nHILO PREVIO (contexto):\n\n{context or '(no hay correos previos)'}"
      f"\n\n=== CORREO NUEVO ===\n\n{self._format(new_message)}"
    )

  def _format(self, message: AgentEmailMessage) -> str:
    return (
      f"From: {message.sender}\nTo: {message.to}\nCc: {message.cc}\n"
      f"Subject: {message.subject}\n\nMessage body: {message.body}"
    )
