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

  email: Optional[str] = None  # null en WhatsApp: ahí la persona se identifica por teléfono
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
Llevas el control del trabajo pendiente de un autónomo o pequeño negocio a partir de su
correo. Una tarea es una acción concreta: lo que el dueño del buzón todavía debe hacer, o lo
que espera que otro haga. Una conversación puede llevar VARIAS tareas: si un correo pide dos
cosas distintas ("envíame el presupuesto y confírmame la fecha"), son dos tareas. No partas
una misma acción en pasos.

QUÉ RECIBES
- TAREAS RELACIONADAS: las tareas que ya existen en las conversaciones con estas personas,
  cada una con su hilo. Las marcadas "(este hilo)" son del hilo del correo nuevo.
- CONVERSACIÓN PREVIA: los últimos correos con las mismas personas, de cualquier hilo, en
  orden cronológico y cada uno con su fecha, hilo y asunto. Es solo contexto.
- CORREO NUEVO: el único que decides. Las tareas cambian o aparecen por lo que dice él.

EL HILO NO ES FIABLE
En el correo la gente no respeta los hilos: responde a un correo viejo para hablar de otra
cosa, abre un correo nuevo con otro asunto para seguir un tema en marcha, reenvía, cambia el
asunto o pierde el "Re:". No relaciones el correo nuevo con una tarea por su hilo ni por su
asunto, sino por el CONTENIDO: de qué habla, quién lo pide, qué documento, importe, fecha,
pedido o proyecto menciona. Una tarea de otro hilo que trata de lo mismo ES la misma tarea:
actualízala con su task_id. Y un mismo hilo puede llevar asuntos que no tienen que ver entre
sí: que el correo esté en el hilo de una tarea no significa que la toque.

LEER UN CORREO
- Lo nuevo es lo que el remitente escribe arriba. Lo citado debajo ("El lunes, Ana
  escribió:", "De: ... Enviado:", líneas con ">") es de correos anteriores: sirve para
  entender, nunca es una petición nueva.
- Ignora firmas, avisos legales, "enviado desde mi iPhone" y pies de página.
- Reenvíos ("---------- Forwarded message", "RV:", "Fwd:"): si alguien le reenvía al dueño
  un correo pidiéndole algo, la petición es para el dueño. Si el dueño reenvía algo a otra
  persona para que se encargue, es una tarea WAITING_RESPONSE: espera a que la otra persona
  responda o lo haga.
- Si el dueño solo va en Cc, el correo casi nunca le pide nada: solo hay tarea si le piden
  algo a él expresamente.
- Respuestas automáticas (fuera de la oficina, acuse de recibo, correo no entregado) no
  crean ni cierran tareas.

DE QUÉ LADO ESTÁS
El correo del dueño del buzón te lo dan al principio del prompt. Si el CORREO NUEVO sale del
dueño, es él quien acaba de responder. Una respuesta del dueño casi nunca crea una tarea:
cambia el estado de la que ya existe ("vale, te lo envío mañana" pasa esa tarea a
WAITING_RESPONSE o le pone fecha). Devuélvela con su task_id; solo es nueva si el dueño se
compromete a algo que no está en TAREAS RELACIONADAS.
Si el dueño ABRE un tema pidiendo algo a otra persona ("¿me mandas el presupuesto?"), es una
tarea WAITING_RESPONSE: él espera la respuesta. Si se compromete a algo ("te paso la propuesta
el lunes"), es TODO. Un correo del dueño que solo informa o agradece no lleva tarea.

QUÉ DEVUELVES
Una LISTA con solo las tareas que el correo nuevo crea o modifica:
- Si toca una tarea que ya existe, sea del hilo que sea, devuélvela con su task_id tal cual
  te lo dan y con los campos como quedan ahora. Nunca crees otra tarea para algo que ya
  existe.
- Si es una acción nueva, devuélvela con task_id null.
- Las tareas que el correo nuevo no toca NO las devuelvas: se quedan como están.
- Si el correo responde a una parte ("te envío el presupuesto") y no a otra, solo la parte
  respondida cambia de estado; la otra no la devuelvas y sigue pendiente.
- Si el correo nuevo no crea ni cambia ninguna tarea, devuelve una lista vacía. No te
  inventes una para rellenar. Newsletters, marketing, notificaciones automáticas, recibos y
  un simple "gracias" no llevan tarea.

Campos:
- title: frase corta con la acción concreta. No es un resumen del correo. Escríbelo siempre
  en el IDIOMA DE LA TAREA que te dan al principio del prompt, aunque el correo esté en otro.
- status TODO: le toca actuar al dueño (una petición, una pregunta, un plazo suyo).
- status WAITING_RESPONSE: el dueño ya respondió o pidió algo y espera a la otra parte.
- status DONE: el correo cierra la acción (entregado, pagado, confirmado, cancelado).
- status TO_VALIDATE: hay algo pendiente pero no sabes de quién es el turno. Si dudas de
  quién es la acción, usa TO_VALIDATE en vez de adivinar.
  El estado describe cómo queda cada tarea tras el correo nuevo, no cómo estaba antes. Si el
  correo pide cambios sobre algo ya cerrado (el presupuesto que enviaste está en DONE y ahora
  te piden cambiarlo), reabre esa tarea en TODO con el título de lo nuevo en vez de crear otra.
- task_id: el id de una de las TAREAS RELACIONADAS si la estás actualizando; null si es
  nueva. Nunca inventes un id.
- contacts: las PERSONAS REALES que intervienen en esa tarea, sin el dueño del buzón. Míralas
  en las cabeceras From, To y Cc del correo nuevo y de los correos previos que tratan de lo
  mismo, y en el cuerpo y las firmas. Una persona, una entrada: no repitas un email.
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
  - email: OBLIGATORIO. Sin email no hay contacto; si solo tienes un nombre suelto,
    descártalo.
  - name: el nombre y apellidos de la persona. Sácalo de la firma, del display name de la
    cabecera ("Ana Pérez <ana@x.com>") o del cuerpo. Si no aparece por ningún sitio, déjalo
    null. Nunca inventes ni deduzcas un nombre a partir del email, y nunca pongas el nombre
    de la empresa como name.
  - phone: el teléfono de la persona si aparece en la firma o en el cuerpo. Si no, null.
    No uses el teléfono general de la empresa como teléfono de la persona.
- due_at: solo cuando el correo da una fecha concreta. Nunca la inventes ni la estimes.
  Resuelve las fechas relativas ("mañana", "la semana que viene", "el viernes") contra la
  fecha del CORREO NUEVO, no contra la de hoy.

ADJUNTOS
Cada correo dice qué ficheros lleva ("Adjuntos: ..."). Las imágenes y PDF del CORREO NUEVO te
llegan detrás del texto, cada uno precedido de su nombre. Míralos para saber si el correo
responde de verdad a lo pedido: si el PDF es la factura o el presupuesto que se esperaba, si
la foto es lo que se pidió. Si el texto dice "te adjunto la factura" pero no hay adjunto, o
el adjunto no es lo pedido, la tarea no se cierra. Saca de ellos las fechas, importes o datos
que solo vengan ahí. Un adjunto sin petición ni respuesta (un logo, una imagen de firma) no
crea tarea.
"""


class AgentService:
  """El agente del correo (solo el webhook de Gmail). El de WhatsApp es WhatsAppAgentService."""

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
      f"- task_id={t.id} | hilo={t.thread_id}{' (este hilo)' if t.thread_id == new_message.thread_id else ''}"
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
