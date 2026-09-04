import logfire
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from bson import ObjectId
from pydantic_ai import Agent

from src.application.use_cases.usage_service import UsageService
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
- title: frase corta con la acción concreta. No es un resumen del correo. Escríbelo siempre
  en el IDIOMA DE LA TAREA que te dan al principio del prompt, aunque el correo esté en otro.
- status TODO: le toca actuar al dueño (una petición, una pregunta, un plazo suyo).
- status WAITING_RESPONSE: el dueño ya respondió y espera a la otra parte.
- status DONE: el hilo cierra la acción (entregado, pagado, confirmado, cancelado).
- status TO_VALIDATE: hay algo pendiente pero no sabes de quién es el turno.
- contacts: TODAS las PERSONAS REALES que intervienen en el hilo, sin el dueño del buzón.
  Recórrete las cabeceras From, To y Cc de todos los correos (el previo y el nuevo) y también
  el cuerpo y las firmas: si una persona aparece con su email, va en la lista. Una persona, una
  entrada: no repitas el mismo email dos veces aunque salga en varios correos.
  SOLO personas, NUNCA empresas ni buzones genéricos. Descarta cualquier email que no
  pertenezca a una persona con nombre y apellidos: info@, ventas@, soporte@, noreply@,
  facturacion@, admin@, contacto@, hola@, y en general cualquier dirección o display name
  que sea el nombre de una empresa, un departamento, una marca, un sistema automático o
  una lista de distribución. Si no puedes identificar a una persona concreta detrás del
  email, no la incluyas.
  EXCEPCIÓN: si un correo llega desde un buzón genérico de empresa pero el cuerpo o la firma
  identifican claramente a la persona que escribe ("Un saludo, Ana Pérez"), sí es un
  contacto: usa ese email genérico como email y el nombre de la persona como name. Lo que
  descartas es la empresa sin nadie detrás, no a la persona que escribe desde ella.
  - email: OBLIGATORIO. Sin email no hay contacto; si solo tienes un nombre suelto, descártalo.
  - name: el nombre y apellidos de la persona. Sácalo de la firma, del display name de la
    cabecera ("Ana Pérez <ana@x.com>") o del cuerpo. Si no aparece por ningún sitio, déjalo
    null. Nunca inventes ni deduzcas un nombre a partir del email, y nunca pongas el nombre
    de la empresa como name.
  - phone: el teléfono de la persona si aparece en la firma o en el cuerpo. Si no, null.
    No uses el teléfono general de la empresa como teléfono de la persona.
- due_at: solo cuando el hilo da una fecha concreta. Nunca la inventes ni la estimes.
  Resuelve las fechas relativas ("mañana", "la semana que viene", "el viernes") contra la
  FECHA DE HOY que te dan al principio del prompt, tomando como referencia el correo nuevo.

El estado describe cómo queda el hilo tras el correo nuevo, no cómo estaba antes. Un hilo
cerrado se reabre si el correo nuevo pide algo más: el presupuesto que enviaste lo dejó en
DONE, pero si ahora te piden cambios, la tarea vuelve a TODO con el título de lo nuevo.

Si el correo nuevo no lleva ninguna tarea, devuelve null. No te inventes una para rellenar.
Newsletters, marketing, notificaciones, recibos y un simple 'gracias' no llevan tarea.
Si dudas de quién es la acción, usa TO_VALIDATE en vez de adivinar.
"""


class AgentService:
  def __init__(self, usage_service: UsageService):
    self.usage_service = usage_service
    self.agent = Agent(
      model= "openai:gpt-5.6-luna",
      output_type=ExtractedTask | None,
      instructions=INSTRUCTIONS
     )

  async def run_tasks(self, user_id: ObjectId, owner_email: str, task_language: str, thread_messages: Optional[List[AgentEmailMessage]], new_message: AgentEmailMessage) -> Optional[ExtractedTask]:
    logfire.info(
      "Agent analyzing thread {thread_id} ({previous} previous mails) from {sender} for owner {owner}",
      thread_id=new_message.thread_id,
      previous=len(thread_messages or []),
      sender=new_message.sender,
      owner=owner_email,
    )
    result = await self.agent.run(self._prompt(owner_email, task_language, thread_messages, new_message))
    
    await self.usage_service.record(
      user_id = user_id,
      email = owner_email,
      model = self.agent.model.model_name,
      result = result
    )

    usage = result.usage
    if not result.output:
      logfire.info(
        "Agent found no task in thread {thread_id} | {tokens} tokens",
        thread_id=new_message.thread_id,
        tokens=usage.total_tokens,
      )
      return None

    task = result.output
    logfire.info(
      "Agent found task in thread {thread_id} | {status} | '{title}' | due={due_at} | contacts={contacts} | {tokens} tokens",
      thread_id=new_message.thread_id,
      status=task.status,
      title=task.title,
      due_at=task.due_at,
      contacts=task.contacts,
      tokens=usage.total_tokens,
    )
    return task

  def _prompt(
    self,
    owner_email: str,
    task_language: str,
    thread_messages: Optional[List[AgentEmailMessage]],
    new_message: AgentEmailMessage,
  ) -> str:

    context = "\n\n---\n\n".join(self._format(m) for m in thread_messages or [])
    return (
      # ponytail: hoy = ahora del worker, no la fecha real del correo; pasar received_at si el retraso importa
      f"FECHA DE HOY: {datetime.now().astimezone().strftime('%A %Y-%m-%d %H:%M %Z')}"
      f"\nDUEÑO DEL BUZÓN: {owner_email}"
      f"\nIDIOMA DE LA TAREA (ISO 639-1): {task_language}"
      f"\n\nHILO PREVIO (contexto):\n\n{context or '(no hay correos previos)'}"
      f"\n\n=== CORREO NUEVO ===\n\n{self._format(new_message)}"
    )

  def _format(self, message: AgentEmailMessage) -> str:
    return (
      f"From: {message.sender}\nTo: {message.to}\nCc: {message.cc}\n"
      f"Subject: {message.subject}\n\nMessage body: {message.body}"
    )
