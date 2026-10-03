import logfire
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from bson import ObjectId
from pydantic_ai import Agent, BinaryContent

from src.application.use_cases.usage_service import UsageService
from src.domain.task import Status, Task
from src.domain.usage import UsageKind

# el cuerpo de un correo de contexto se corta aquí: Gmail trae las respuestas anteriores
# citadas debajo, y esas ya están en la conversación como sus propios correos.
# ponytail: recorte por caracteres; quitar la cita de verdad si se pierde texto útil
CONTEXT_BODY_CHARS = 3000

@dataclass
class ExtractedContact:
  """Contact sin ids: el LLM no puede rellenar un ObjectId."""

  email: Optional[str] = None
  name: Optional[str] = None
  phone: Optional[str] = None


@dataclass
class ExtractedTask:
  # id de una de las TAREAS RELACIONADAS cuando el correo la actualiza; null si es nueva.
  # Primero y sin default: con default el modelo lo omitía y duplicaba la tarea
  task_id: Optional[str]
  title: str
  status: Status
  contacts: List[ExtractedContact]
  due_at: Optional[datetime] = None

@dataclass
class AgentAttachment:
  filename: str
  mime_type: str
  # el contenido solo va en el correo nuevo cuando el modelo puede leerlo (imagen, PDF);
  # el resto, y toda la conversación previa, le llega por nombre
  data: Optional[bytes] = None


@dataclass
class AgentEmailMessage:
  thread_id: str
  sender: str
  to: str
  subject: str
  body: str
  sent_at: datetime
  cc: Optional[str] = None
  attachments: List[AgentAttachment] = field(default_factory=list)

INSTRUCTIONS = """
Extraes tareas del correo de un autónomo o pequeño negocio (el DUEÑO del buzón).
Una tarea = una acción concreta que el dueño debe hacer o que espera que otro haga.

ENTRADA
- TAREAS RELACIONADAS: tareas existentes con estas personas y sus hilos. "(este hilo)" = está
  también en el hilo del correo nuevo.
- CONVERSACIÓN PREVIA: últimos correos con estas personas, de cualquier hilo. Solo contexto.
- CORREO NUEVO: lo único que analizas. Solo él crea o cambia tareas.

SALIDA
Una lista con SOLO las tareas que el correo nuevo crea o cambia.
- Actualiza una existente: su task_id exacto y los campos como quedan ahora.
- Acción nueva: task_id null.
- No devuelvas tareas que el correo no toca.
- Nada que hacer: lista vacía. No inventes tareas.

REGLAS
1. Relaciona por CONTENIDO (tema, persona, documento, importe, fecha, pedido, proyecto),
   NUNCA por hilo ni asunto. Misma acción en otro hilo = misma tarea: usa su task_id.
   Estar en el hilo de una tarea no implica tocarla.
2. Una petición distinta = una tarea ("envíame el presupuesto y confírmame la fecha" = 2).
   No partas una acción en pasos.
3. Nunca dupliques: si la acción ya existe, actualízala.
4. Si el correo responde solo a una parte, cambia solo esa tarea; la otra no la devuelvas.
5. Petición sobre algo en DONE ("cámbiame el presupuesto"): reabre esa tarea en TODO con el
   título nuevo. No crees otra.

CÓMO LEER EL CORREO
- Solo cuenta lo escrito arriba. Lo citado ("Ana escribió:", "De: ... Enviado:", ">") es
  contexto, nunca una petición nueva.
- Ignora firmas, avisos legales, "enviado desde mi iPhone" y pies.
- Reenvío al dueño pidiéndole algo: la petición es para el dueño (TODO).
- Reenvío del dueño a otro para que se encargue: WAITING_RESPONSE.
- Dueño solo en Cc: sin tarea, salvo que le pidan algo a él expresamente.
- Sin tarea: respuestas automáticas (fuera de oficina, acuse, no entregado), newsletters,
  marketing, notificaciones, recibos, "gracias".

SI EL CORREO NUEVO LO ENVÍA EL DUEÑO (su email va al principio del prompt)
- Responde a algo existente: actualiza esa tarea, no crees otra ("te lo envío mañana" →
  WAITING_RESPONSE o due_at).
- Pide algo a otro: tarea nueva WAITING_RESPONSE.
- Se compromete a algo nuevo: tarea nueva TODO.
- Solo informa o agradece: sin tarea.

CAMPOS
- task_id: id de TAREAS RELACIONADAS o null. Nunca inventes uno.
- title: frase corta con la acción, no un resumen. Siempre en el IDIOMA DE LA TAREA del
  prompt, aunque el correo esté en otro.
- status (cómo queda tras el correo nuevo):
  - TODO: le toca actuar al dueño.
  - WAITING_RESPONSE: el dueño espera a la otra parte.
  - DONE: la acción se cerró (entregado, pagado, confirmado, cancelado).
  - TO_VALIDATE: no está claro de quién es el turno. Úsalo antes que adivinar.
- due_at: solo con fecha concreta en el correo. Nunca la estimes. Resuelve fechas relativas
  ("mañana", "el viernes") contra la fecha del CORREO NUEVO, no la de hoy.
- contacts: personas reales de esa tarea, sin el dueño. Búscalas en From/To/Cc del correo
  nuevo y de los previos del mismo tema, y en cuerpo y firmas. Una entrada por email.
  - Solo personas. Descarta empresas, marcas, departamentos, listas y buzones genéricos
    (info@, ventas@, soporte@, noreply@, facturacion@, admin@, contacto@, hola@...).
  - Excepción: buzón genérico firmado por una persona ("Un saludo, Ana Pérez") → contacto
    con ese email y el nombre de la persona.
  - email: obligatorio. Sin email, descarta.
  - name: nombre y apellidos de firma, display name o cuerpo; si no aparece, null. Nunca lo
    deduzcas del email ni pongas el de la empresa.
  - phone: el de la persona si aparece; si no, null. Nunca el general de la empresa.

ADJUNTOS
- Cada correo lista sus ficheros ("Adjuntos: ..."). Las imágenes y PDF del CORREO NUEVO van
  tras el texto, precedidos de su nombre.
- Comprueba que el adjunto es lo pedido (factura, presupuesto, foto). Si falta o no lo es,
  la tarea NO se cierra.
- Saca de ellos fechas, importes y datos que solo estén ahí.
- Adjunto sin petición ni respuesta (logo, imagen de firma): sin tarea.
"""


class AgentService:
  def __init__(self, usage_service: UsageService):
    self.usage_service = usage_service
    self.agent = Agent(
      model= "openai:gpt-5.6-luna",
      output_type=List[ExtractedTask],
      instructions=INSTRUCTIONS
     )

  async def run_tasks(
    self,
    user_id: ObjectId,
    owner_email: str,
    task_language: str,
    context: List[AgentEmailMessage],
    new_message: AgentEmailMessage,
    tasks: List[Task],
  ) -> List[ExtractedTask]:
    logfire.info(
      "Agent analyzing mail in thread {thread_id} ({previous} context mails, {tasks} related tasks) from {sender} for owner {owner}",
      thread_id=new_message.thread_id,
      previous=len(context),
      tasks=len(tasks),
      sender=new_message.sender,
      owner=owner_email,
    )
    # los adjuntos legibles van detrás del texto, cada uno con su nombre delante
    files = [
      part
      for a in new_message.attachments
      if a.data
      for part in (f"Adjunto {a.filename}:", BinaryContent(data=a.data, media_type=a.mime_type))
    ]
    result = await self.agent.run(
      [self._prompt(owner_email, task_language, context, new_message, tasks), *files]
    )

    await self.usage_service.record(
      user_id = user_id,
      email = owner_email,
      model = self.agent.model.model_name,
      kind = UsageKind.TASK,
      result = result
    )

    usage = result.usage
    extracted = result.output or []
    logfire.info(
      "Agent found {count} tasks for mail in thread {thread_id} | {tasks} | {tokens} tokens",
      count=len(extracted),
      thread_id=new_message.thread_id,
      tasks=[
        {"task_id": t.task_id, "status": t.status, "title": t.title, "due_at": t.due_at, "contacts": t.contacts}
        for t in extracted
      ],
      tokens=usage.total_tokens,
    )
    return extracted

  def _prompt(
    self,
    owner_email: str,
    task_language: str,
    context: List[AgentEmailMessage],
    new_message: AgentEmailMessage,
    tasks: List[Task],
  ) -> str:
    previous = "\n\n---\n\n".join(self._format(m, CONTEXT_BODY_CHARS) for m in context)
    related = "\n".join(
      f"- task_id={t.id} | hilos={', '.join(t.thread_ids)}{' (este hilo)' if new_message.thread_id in t.thread_ids else ''}"
      f" | status={t.status} | title={t.title} | due_at={t.due_at}"
      for t in tasks
    )
    return (
      f"FECHA DE HOY: {datetime.now().astimezone().strftime('%A %Y-%m-%d %H:%M %Z')}"
      f"\nDUEÑO DEL BUZÓN: {owner_email}"
      f"\nIDIOMA DE LA TAREA (ISO 639-1): {task_language}"
      f"\n\nTAREAS RELACIONADAS:\n{related or '(no hay tareas con estas personas)'}"
      f"\n\nCONVERSACIÓN PREVIA (contexto):\n\n{previous or '(no hay correos previos con estas personas)'}"
      f"\n\n=== CORREO NUEVO ===\n\n{self._format(new_message)}"
    )

  def _format(self, message: AgentEmailMessage, max_body: Optional[int] = None) -> str:
    attachments = ", ".join(a.filename for a in message.attachments) or "ninguno"
    body = message.body
    if max_body and len(body) > max_body:
      body = body[:max_body] + "\n[... recortado]"
    return (
      f"Fecha: {message.sent_at.strftime('%A %Y-%m-%d %H:%M %Z')}\nHilo: {message.thread_id}\n"
      f"From: {message.sender}\nTo: {message.to}\nCc: {message.cc}\n"
      f"Subject: {message.subject}\nAdjuntos: {attachments}\n\nMessage body: {body}"
    )
