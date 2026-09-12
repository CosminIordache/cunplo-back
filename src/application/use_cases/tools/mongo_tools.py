import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import logfire
from bson import ObjectId
from pydantic_ai import RunContext

# lo que el asistente puede leer; nada de users ni usages (gasto)
COLLECTIONS = {"tasks", "contacts", "messages"}
# operadores que ejecutan código o saltan el filtro: fuera
FORBIDDEN = {"$where", "$function", "$accumulator", "$expr"}

# lo que no aporta al asistente y solo gasta tokens; el body se pide aparte
HIDDEN = {
  "tasks": {"user_id": 0},
  "contacts": {"user_id": 0, "updated_at": 0},
  "messages": {"user_id": 0, "provider_id": 0, "created_at": 0, "body": 0},
}
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
# ponytail: un correo normal cabe de sobra; el resto se lee por _id si hace falta
BODY_CHARS = 2000

_OBJECT_ID = re.compile(r"^[0-9a-f]{24}$")


@dataclass
class MongoDeps:
  """Lo que la tool necesita: la db y el usuario al que se acota todo."""

  user_id: ObjectId
  db: Any  # AsyncIOMotorDatabase; Any para no importar motor en use_cases


def _decode(value: Any) -> Any:
  """El LLM manda JSON: ids como hex y fechas ISO se vuelven tipos de Mongo."""
  if isinstance(value, dict):
    for key in value:
      if key in FORBIDDEN:
        raise ValueError(f"operator {key} not allowed")
    return {k: _decode(v) for k, v in value.items()}
  if isinstance(value, list):
    return [_decode(v) for v in value]
  if isinstance(value, str):
    if _OBJECT_ID.match(value):
      return ObjectId(value)
    try:
      return datetime.fromisoformat(value)
    except ValueError:
      return value
  return value


def _encode(value: Any) -> Any:
  if isinstance(value, dict):
    return {k: _encode(v) for k, v in value.items()}
  if isinstance(value, list):
    return [_encode(v) for v in value]
  if isinstance(value, ObjectId):
    return str(value)
  if isinstance(value, datetime):
    return value.isoformat()
  return value


async def find(
  ctx: RunContext[MongoDeps],
  collection: str,
  status: str,
  filter: Optional[dict] = None,
  projection: Optional[dict] = None,
  sort: Optional[dict] = None,
  limit: int = DEFAULT_LIMIT,
) -> list[dict]:
  """Consulta de SOLO LECTURA (find) sobre una colección del usuario.

  - collection: "tasks", "contacts" o "messages".
  - status: frase corta, en el IDIOMA DE LA PREGUNTA y en su tono, que describe lo que estás
    buscando; se le muestra mientras esperas el resultado. Concreta, sin tecnicismos:
    "Buscando el correo de Pablo", "Mirando qué tienes pendiente para mañana".
  - filter: filtro de Mongo en JSON. Los ids van como string hex de 24 caracteres y las
    fechas como ISO 8601; se convierten solos. Nunca hace falta poner user_id.
  - projection: campos a devolver, p.ej. {"title": 1, "status": 1}. Sin projection se
    devuelven los campos útiles; en "messages" el body NO viene: pídelo con
    {"body": 1, "subject": 1} y filtrando por _id solo cuando necesites leer el correo.
    El body se recorta a 2000 caracteres.
  - sort: p.ej. {"due_at": 1}.
  - limit: máximo de documentos (por defecto 20, tope 50). Filtra y ordena en vez de
    pedir más.
  """
  if collection not in COLLECTIONS:
    raise ValueError(f"collection {collection} not allowed")
  # el user_id va después: aunque el filtro traiga otro, gana el del usuario que pregunta
  query = {**_decode(filter or {}), "user_id": ctx.deps.user_id}
  logfire.info("Assistant query on {collection}: {query}", collection=collection, query=query)
  # solo find: desde aquí no hay forma de escribir ni borrar
  cursor = ctx.deps.db[collection].find(query, projection or HIDDEN[collection])
  if sort:
    cursor = cursor.sort(list(sort.items()))
  # ponytail: tope fijo; paginación con skip si el asistente necesita recorrer más
  docs = await cursor.to_list(length=max(1, min(limit, MAX_LIMIT)))
  for doc in docs:
    if isinstance(doc.get("body"), str):
      doc["body"] = doc["body"][:BODY_CHARS]
  return [_encode(d) for d in docs]


async def _threads(db, user_id: ObjectId, keys: list[dict], with_body: bool) -> list[dict]:
  """keys: [{"integration_id", "thread_id"}]. Una entrada por hilo con tarea, correos y contactos."""
  if not keys:
    return []
  query = {"user_id": user_id, "$or": keys}
  # tres consultas, todas por índice; una sola vuelta del modelo
  tasks = await db["tasks"].find(query, HIDDEN["tasks"]).to_list(length=MAX_LIMIT)
  projection = dict(HIDDEN["messages"])
  if with_body:
    projection.pop("body")
  messages = await (
    db["messages"].find(query, projection).sort("internal_date", 1).to_list(length=MAX_LIMIT)
  )
  contact_ids = {c for t in tasks for c in t.get("contact_ids", [])}
  contacts = await db["contacts"].find(
    {"user_id": user_id, "_id": {"$in": list(contact_ids)}}, HIDDEN["contacts"]
  ).to_list(length=MAX_LIMIT)
  by_id = {c["_id"]: c for c in contacts}
  task_by_key = {(t["integration_id"], t["thread_id"]): t for t in tasks}
  result = []
  for key in keys:
    pair = (key["integration_id"], key["thread_id"])
    task = task_by_key.get(pair)
    thread_messages = [m for m in messages if (m["integration_id"], m["thread_id"]) == pair]
    for m in thread_messages:
      if isinstance(m.get("body"), str):
        m["body"] = m["body"][:BODY_CHARS]
    result.append({
      "task": task,
      "messages": thread_messages,
      "contacts": [by_id[c] for c in (task or {}).get("contact_ids", []) if c in by_id],
    })
  return _encode(result)


async def thread_context(
  ctx: RunContext[MongoDeps],
  status: str,
  integration_id: str,
  thread_ids: list[str],
  with_body: bool = False,
) -> list[dict]:
  """Todo lo de uno o varios hilos EN UNA sola llamada: su tarea, sus correos y sus contactos.

  Úsala en cuanto tengas thread_id e integration_id (p.ej. tras un find en tasks) en vez
  de encadenar finds. Devuelve una entrada por hilo con
  {"task": {...} | null, "messages": [...ordenados por fecha...], "contacts": [...]}.
  - status: frase corta para el usuario, en su idioma, como en find.
  - integration_id: la cuenta; los thread_ids solo son únicos dentro de ella.
  - with_body: true solo cuando necesites leer el texto de los correos.
  """
  integration = _decode(integration_id)
  keys = [{"integration_id": integration, "thread_id": t} for t in thread_ids[:MAX_LIMIT]]
  logfire.info("Assistant thread context: {keys}", keys=keys)
  return await _threads(ctx.deps.db, ctx.deps.user_id, keys, with_body)


async def conversations_with(
  ctx: RunContext[MongoDeps],
  status: str,
  person: str,
  since: Optional[str] = None,
  with_body: bool = False,
) -> dict:
  """Todo lo hablado con una persona EN UNA sola llamada: sus tareas, correos y contacto.

  Es la tool para "qué he hablado con X", "qué tengo pendiente con X", "el correo de X".
  - person: nombre o email tal como lo dice el usuario.
  - since: fecha ISO 8601 opcional; solo hilos con correos desde entonces.
  - with_body: true solo cuando necesites leer el texto de los correos.
  Devuelve {"contact": {...} | null, "threads": [...como thread_context...]}. Si hay varios
  contactos que encajan devuelve {"candidates": [...]} y debes pedir aclaración.
  """
  db, user_id = ctx.deps.db, ctx.deps.user_id
  logfire.info("Assistant conversations with {person}", person=person)
  # regex acotado por user_id: un usuario tiene decenas de contactos, no hace falta más
  match = {"$regex": re.escape(person), "$options": "i"}
  contacts = await db["contacts"].find(
    {"user_id": user_id, "$or": [{"name": match}, {"email": match}]}, HIDDEN["contacts"]
  ).to_list(length=10)
  if len(contacts) > 1:
    return {"candidates": _encode(contacts)}
  contact = contacts[0] if contacts else None
  if contact:
    # relación ya resuelta por el worker: igualdad por índice, sin buscar texto
    tasks = await db["tasks"].find(
      {"user_id": user_id, "contact_ids": contact["_id"]}, {"integration_id": 1, "thread_id": 1}
    ).to_list(length=MAX_LIMIT)
    keys = [{"integration_id": t["integration_id"], "thread_id": t["thread_id"]} for t in tasks]
  else:
    # ponytail: sin contacto, cabeceras crudas; acotado a los correos del usuario
    query = {"user_id": user_id, "$or": [{"sender": match}, {"to": match}, {"cc": match}]}
    messages = await db["messages"].find(
      query, {"integration_id": 1, "thread_id": 1}
    ).to_list(length=MAX_LIMIT)
    keys = list({(m["integration_id"], m["thread_id"]): None for m in messages})
    keys = [{"integration_id": i, "thread_id": t} for i, t in keys]
  threads = await _threads(db, user_id, keys, with_body)
  if since:
    floor = datetime.fromisoformat(since).timestamp() * 1000
    threads = [t for t in threads if any(m["internal_date"] >= floor for m in t["messages"])]
  return {"contact": _encode(contact), "threads": threads}
