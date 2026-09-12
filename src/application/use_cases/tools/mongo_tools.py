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
  - status: frase corta, en el IDIOMA del usuario y en su tono, que describe lo que estás
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
