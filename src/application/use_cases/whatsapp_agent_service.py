import logfire
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from bson import ObjectId
from pydantic_ai import Agent, BinaryContent

from src.application.use_cases.agent_service import AgentAttachment, ExtractedTask
from src.application.use_cases.usage_service import UsageService
from src.domain.task import Task
from src.domain.usage import UsageKind


@dataclass
class AgentWhatsAppMessage:
  thread_id: str
  sender: str
  to: str
  body: str
  attachments: List[AgentAttachment] = field(default_factory=list)


INSTRUCTIONS = """
Llevas el control del trabajo pendiente de un autónomo o pequeño negocio a partir de sus chats
de WhatsApp. Una tarea es una acción concreta: lo que el dueño todavía debe hacer, o lo que
espera de la otra persona.

En WhatsApp no hay hilos ni asuntos: cada chat es UNA sola conversación con una persona que
dura meses y en la que se van tratando temas distintos. Todo lo que recibes es de ese mismo
chat:
- TAREAS DEL CHAT: todas las tareas que ya salieron de este chat, de cualquier tema.
- MENSAJES ANTERIORES: los últimos mensajes del chat, solo como contexto.
- MENSAJES NUEVOS: lo llegado desde el último análisis. Suele ser una ráfaga ("hola" + la
  petición real, o una petición partida en varios mensajes): analízalos juntos, como un todo.
Decide sobre los MENSAJES NUEVOS qué tareas cambian o aparecen; los anteriores solo sirven
para entender a qué se refieren.

Como todo está en el mismo chat, relaciona un mensaje con una tarea por su CONTENIDO, nunca
por estar en la conversación: "¿y lo de la factura?" toca la tarea de la factura aunque haya
otras abiertas; un tema nuevo es una tarea nueva aunque el chat ya tenga tareas. Una
respuesta corta ("ok", "hecho", "mañana te lo mando") se refiere a lo último que se habló:
mira los mensajes anteriores para saber a qué tarea va. Si no puedes saber a cuál de varias
tareas abiertas se refiere, no toques ninguna.
Los chats son largos: puede que el contexto no llegue al origen de una tarea. Fíate de TAREAS
DEL CHAT para saber qué está abierto.

Un chat puede llevar VARIAS tareas: si piden dos cosas distintas ("mándame el presupuesto y
confírmame la fecha"), son dos tareas. No partas una misma acción en pasos, ni crees una
tarea por cada mensaje de la ráfaga.

Devuelves una LISTA con solo las tareas que los mensajes nuevos crean o modifican:
- Si tocan una tarea que ya existe, devuélvela con su task_id tal cual te lo dan y con los
  campos como quedan ahora. Nunca crees otra tarea para algo que ya existe.
- Si es una acción nueva, devuélvela con task_id null.
- Las tareas que los mensajes nuevos no tocan NO las devuelvas: se quedan como están.
- Si el dueño responde a una parte ("te envío el presupuesto") y no a otra, solo la parte
  respondida cambia de estado; la otra no la devuelvas y sigue pendiente.

El teléfono del dueño te lo dan al principio del prompt. Fíjate en él para saber de qué lado
estás: los mensajes con From igual a su número los escribe él.
Un mensaje del dueño casi nunca crea una tarea: cambia el estado de la que ya existe ("vale,
te lo envío mañana" pasa esa tarea a WAITING_RESPONSE o le pone fecha). Solo es nueva si el
dueño pide algo ("¿me mandas el presupuesto?" → WAITING_RESPONSE, espera la respuesta) o se
compromete a algo ("te paso la propuesta el lunes" → TODO) que no está en TAREAS DEL CHAT.
Un mensaje del dueño que solo informa o agradece no lleva tarea.

Campos:
- title: frase corta con la acción concreta. No es un resumen del chat. Escríbelo siempre en
  el IDIOMA DE LA TAREA que te dan al principio del prompt, aunque el chat esté en otro.
- status TODO: le toca actuar al dueño (una petición, una pregunta, un plazo suyo).
- status WAITING_RESPONSE: el dueño ya respondió y espera a la otra parte.
- status DONE: el chat cierra la acción (entregado, pagado, confirmado, cancelado).
- status TO_VALIDATE: hay algo pendiente pero no sabes de quién es el turno.
- task_id: el id de una de las TAREAS DEL CHAT si la estás actualizando; null si es nueva.
  Nunca inventes un id.
- contacts: la persona del chat, sin el dueño, y cualquier otra persona real que los mensajes
  presenten con su teléfono o email ("llama a Ana Pérez, 600 123 456"). Una persona, una
  entrada. SOLO personas, NUNCA empresas, departamentos ni sistemas automáticos.
  - phone: OBLIGATORIO. Para la persona del chat es el número entre <> de su From (o el To
    si el mensaje es del dueño), copiado tal cual con su "+". Para otra persona, el que
    escriba el mensaje; sin teléfono no la incluyas.
  - name: el nombre que aparece delante del número en el From o el que se dé en el chat. Si
    no aparece, null. Nunca lo inventes ni pongas el nombre de una empresa.
  - email: null salvo que el mensaje escriba el email de esa persona.
- due_at: solo cuando el chat da una fecha concreta. Nunca la inventes ni la estimes.
  Resuelve las fechas relativas ("mañana", "la semana que viene", "el viernes") contra la
  FECHA DE HOY que te dan al principio del prompt.

El estado describe cómo queda cada tarea tras los mensajes nuevos, no cómo estaba antes. Si
piden cambios sobre algo ya cerrado (el presupuesto está en DONE y ahora quieren cambiarlo),
reabre esa tarea en TODO con el título de lo nuevo en vez de crear otra.

Si los mensajes nuevos no crean ni cambian ninguna tarea, devuelve una lista vacía. No te
inventes una para rellenar. Un saludo, un "ok", un "👍", un "gracias", cadenas reenviadas,
publicidad o charla personal no llevan tarea, salvo que cierren o confirmen una de las
TAREAS DEL CHAT.
Si dudas de quién es la acción, usa TO_VALIDATE en vez de adivinar.

Adjuntos: van en el propio mensaje ("[Imagen]", "[Documento PDF]", "[Nota de voz: ...]" ya
transcrita, con su texto o caption debajo). Las imágenes y PDF de los MENSAJES NUEVOS te
llegan detrás del texto, cada uno precedido de su nombre y su remitente. Míralos para saber
si el mensaje responde de verdad a lo pedido: si el PDF es la factura o el presupuesto que se
esperaba, si la foto es lo que se pidió. Si dice "te mando la factura" pero no hay adjunto, o
el adjunto no es lo pedido, la tarea no se cierra. Saca de ellos las fechas, importes o datos
que solo vengan ahí. Una nota de voz cuenta como un mensaje de texto más. Un adjunto sin
petición ni respuesta (un sticker, un meme) no crea tarea.
"""


class WhatsAppAgentService:
  """El agente de los chats de WhatsApp. El de correo es AgentService: se separaron para
  especializar cada uno sin que el otro cambie."""

  def __init__(self, usage_service: UsageService):
    self.usage_service = usage_service
    self.agent = Agent(
      model="openai:gpt-5.6-luna",
      output_type=List[ExtractedTask],
      instructions=INSTRUCTIONS,
    )

  async def run_tasks(
    self,
    user_id: ObjectId,
    owner_phone: str,
    task_language: str,
    thread_messages: Optional[List[AgentWhatsAppMessage]],
    new_messages: List[AgentWhatsAppMessage],
    thread_tasks: Optional[List[Task]] = None,
  ) -> List[ExtractedTask]:
    # la ráfaga entera desde el último análisis
    new_message = new_messages[-1]
    logfire.info(
      "Agent analyzing chat {thread_id} ({previous} previous messages, {tasks} tasks) from {sender} for owner {owner}",
      thread_id=new_message.thread_id,
      previous=len(thread_messages or []),
      tasks=len(thread_tasks or []),
      sender=new_message.sender,
      owner=owner_phone,
    )
    files = [
      part
      for m in new_messages
      for a in m.attachments
      if a.data
      for part in (f"Adjunto {a.filename} ({m.sender}):", BinaryContent(data=a.data, media_type=a.mime_type))
    ]
    result = await self.agent.run(
      [self._prompt(owner_phone, task_language, thread_messages, new_messages, thread_tasks), *files]
    )

    await self.usage_service.record(
      user_id=user_id,
      email=owner_phone,
      model=self.agent.model.model_name,
      kind=UsageKind.TASK,
      result=result,
    )

    tasks = result.output or []
    logfire.info(
      "Agent found {count} tasks in chat {thread_id} | {tasks} | {tokens} tokens",
      count=len(tasks),
      thread_id=new_message.thread_id,
      tasks=[
        {"task_id": t.task_id, "status": t.status, "title": t.title, "due_at": t.due_at, "contacts": t.contacts}
        for t in tasks
      ],
      tokens=result.usage.total_tokens,
    )
    return tasks

  def _prompt(
    self,
    owner_phone: str,
    task_language: str,
    thread_messages: Optional[List[AgentWhatsAppMessage]],
    new_messages: List[AgentWhatsAppMessage],
    thread_tasks: Optional[List[Task]] = None,
  ) -> str:
    context = "\n\n---\n\n".join(self._format(m) for m in thread_messages or [])
    new = "\n\n---\n\n".join(self._format(m) for m in new_messages)
    tasks = "\n".join(
      f"- task_id={t.id} | status={t.status} | title={t.title} | due_at={t.due_at}"
      for t in thread_tasks or []
    )
    return (
      # ponytail: hoy = ahora del worker, no la fecha real del mensaje
      f"FECHA DE HOY: {datetime.now().astimezone().strftime('%A %Y-%m-%d %H:%M %Z')}"
      f"\nTELÉFONO DEL DUEÑO: {owner_phone}"
      f"\nIDIOMA DE LA TAREA (ISO 639-1): {task_language}"
      f"\n\nTAREAS DEL CHAT:\n{tasks or '(el chat no tiene tareas todavía)'}"
      f"\n\nMENSAJES ANTERIORES (contexto):\n\n{context or '(no hay mensajes anteriores)'}"
      f"\n\n=== MENSAJES NUEVOS ===\n\n{new}"
    )

  def _format(self, message: AgentWhatsAppMessage) -> str:
    # el adjunto ya va en el body ("[Imagen]") y GOWA le cambia el nombre
    return f"From: {message.sender}\nTo: {message.to}\n\n{message.body}"
