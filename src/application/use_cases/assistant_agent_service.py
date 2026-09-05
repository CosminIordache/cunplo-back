import json
import logfire
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from bson import ObjectId
from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import FunctionToolCallEvent, ThinkingPart
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

from src.application.use_cases.tools import mongo_tools
from src.application.use_cases.tools.mongo_tools import MongoDeps
from src.application.use_cases.usage_service import UsageService


@dataclass
class AssistantAnswer:
  answer: str
  task_ids: list[str]
  contact_ids: list[str]
  message_ids: list[str]


INSTRUCTIONS = """
Eres el asistente personal de un autónomo o pequeño negocio. Conoces sus tareas, sus
contactos y los correos de los que salen, y le respondes como lo haría una persona de
confianza que trabaja con él.

Tono: habla como en una conversación normal, de tú, natural y cercano. Nada de listados
técnicos, ids, nombres de campos ni de colecciones en el texto: eso va aparte en la salida
estructurada. Cuenta las cosas como se las contarías en persona ("Con Pablo has hablado
dos veces esta semana desde tu cuenta de Gmail: el martes te pidió el presupuesto y ayer
le contestaste. Sigue pendiente que se lo mandes."). Si no hay nada, dilo con naturalidad
("No tengo ningún correo con Pablo estos días."). Sé breve: lo justo para responder, sin
saludos de relleno ni cierres tipo "¿algo más?".

Consulta los datos con la tool `find` (solo lectura): nunca inventes nada. Responde en el
IDIOMA que te dan al principio del prompt y resuelve las fechas relativas ("esta semana",
"mañana") contra la FECHA DE HOY.

Colecciones y campos:
- tasks: _id, title, status (todo | waiting_response | done | to_validate), priority
  (low | medium | high | urgent | null), due_at (fecha o null), contact_ids (ids de contacts),
  thread_id, integration_id, created_at, updated_at.
- contacts: _id, name, email, phone, created_at.
- messages: _id, thread_id, integration_id, sender, to, cc, subject, body, internal_date.
  Solo se guardan los correos de hilos que generaron una tarea, no todo el buzón.
  Para leer el correo de una tarea filtra por su thread_id e integration_id.
  sender, to y cc son cabeceras crudas ("Ana Pérez <ana@x.com>"): para buscar los correos
  con una persona usa $regex con su email (busca antes el email en contacts si te dan un
  nombre), y mira tanto sender como to con $or.
  internal_date es epoch en MILISEGUNDOS: usa HOY EN EPOCH MS del prompt y resta
  86400000 por cada día ("hace 3 días" -> {"internal_date": {"$gte": hoy - 3*86400000}}).
  Cada mensaje pertenece al hilo de una tarea: cuando respondas con mensajes, busca SIEMPRE
  su tarea en tasks por (thread_id, integration_id), menciónala en la respuesta e incluye
  su _id en task_ids. Con varios hilos, una consulta con $or o $in sobre thread_id.
Estados: todo (le toca actuar al dueño), waiting_response (espera a la otra parte),
done (cerrada), to_validate (pendiente sin saber de quién es el turno).

Salida: `answer` es el texto para el usuario. En `task_ids`, `contact_ids` y `message_ids`
devuelve los `_id` EXACTOS de las tareas, contactos y mensajes que usas en la respuesta. Nunca inventes ids ni incluyas
los que no vengan al caso; listas vacías si no usaste ninguno.
"""


def _line(obj: dict) -> bytes:
  return json.dumps(obj).encode() + b"\n"


class AssistantService:
  def __init__(self, db, usage_service: UsageService):
    self.db = db
    self.usage_service = usage_service
    self.agent = Agent(
      model="openai:gpt-5.6-luna",
      deps_type=MongoDeps,
      output_type=AssistantAnswer,
      instructions=INSTRUCTIONS,
      tools=[mongo_tools.find],
      # sin esto OpenAI no devuelve el razonamiento y no habría nada que emitir
      model_settings=OpenAIResponsesModelSettings(openai_reasoning_summary="detailed"),
    )

  def _prompt(self, language: str, question: str) -> str:
    now = datetime.now().astimezone()
    return (
      f"FECHA DE HOY: {now.strftime('%A %Y-%m-%d %H:%M %Z')}"
      f"\nHOY EN EPOCH MS: {int(now.timestamp() * 1000)}"
      f"\nIDIOMA (ISO 639-1): {language}"
      f"\n\nPREGUNTA: {question}"
    )

  async def ask_stream(
    self, user_id: ObjectId, email: str, language: str, question: str
  ) -> AsyncIterator[bytes]:
    """
    - {"thinking": "..."} trozos del razonamiento del modelo
    - {"status": "Buscando el correo de Pablo", "collection": "messages"}  cada consulta
    - {"delta": "..."} trozos del texto de la respuesta
    - {"task_ids", "contact_ids", "message_ids"} la última, con los ids finales."""
    
    logfire.info("Assistant question (stream) from {email}: {question}", email=email, question=question)
    
    sent = ""
    
    deps = MongoDeps(user_id=user_id, db=self.db)
    
    # iter() recorre el bucle del agente nodo a nodo: así vemos las tool calls, que run_stream esconde
    async with self.agent.iter(self._prompt(language, question), deps=deps) as run:
      async for node in run:
        if Agent.is_call_tools_node(node):
          async with node.stream(run.ctx) as events:
            async for event in events:
              if isinstance(event, FunctionToolCallEvent):
                args = event.part.args_as_dict()
                # el modelo redacta el status: es lo que ve el usuario mientras busca
                yield _line({"status": args.get("status"), "collection": args.get("collection")})
        elif Agent.is_model_request_node(node):
          thought = ""  # el razonamiento es por petición: cada vuelta empieza de cero
          async with node.stream(run.ctx) as stream:
            # snapshots de la respuesta: de cada uno sacamos lo que ha crecido
            async for response in stream.stream_response(debounce_by=0.1):
              thinking = "".join(p.content for p in response.parts if isinstance(p, ThinkingPart))
              
              if thinking.startswith(thought) and len(thinking) > len(thought):
                yield _line({"thinking": thinking[len(thought):]})
                thought = thinking
              # la salida estructurada llega como JSON parcial: solo valida en la respuesta final
              
              try:
                partial = await stream.validate_response_output(response, allow_partial=True)
              except (UnexpectedModelBehavior, ValidationError, ModelRetry):
                continue
              
              text = partial.answer or ""
              
              if text.startswith(sent) and len(text) > len(sent):
                yield _line({"delta": text[len(sent):]})
                sent = text
      
      result = run.result
    
    await self.usage_service.record(
      user_id=user_id, email=email, model=self.agent.model.model_name, result=result
    )
    
    answer = result.output
    
    if len(answer.answer) > len(sent):  # lo que el debounce se dejara por emitir
      yield _line({"delta": answer.answer[len(sent):]})
    
    yield _line({
      "task_ids": answer.task_ids, "contact_ids": answer.contact_ids, "message_ids": answer.message_ids,
    })
    
    logfire.info(
      "Assistant streamed answer to {email} | tasks={tasks} contacts={contacts} messages={messages}",
      email=email, tasks=answer.task_ids, contacts=answer.contact_ids, messages=answer.message_ids,
    )
