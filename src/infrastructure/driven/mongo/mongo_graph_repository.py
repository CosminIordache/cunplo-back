import json
from typing import AsyncIterator

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

# Los emails de las cabeceras, que llegan crudas ("Nombre <email@dominio>").
# ponytail: regex, no un parser de RFC 5322. Basta para sacar direcciones de un
# From/To/Cc normal; los nombres entre comillas con '@' dentro darían un falso
# positivo, que al no existir en contacts se descarta solo
EMAIL = r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w-]+(?:\.[\w-]+)+"


class MongoGraphRepository:
  """El grafo no tiene colección propia: los nodos son las tareas, sus contactos
  y los correos de su hilo. Las aristas contacto→tarea salen de Task.contact_ids,
  las tarea→mensaje del hilo, y las contacto→mensaje de las cabeceras del correo:
  quien iba en el To o el Cc participó aunque el agente no lo ligara a la tarea.
  Se arma con una agregación para no traerse las tres colecciones al proceso."""

  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["tasks"]

  # Por tarea: su nodo, sus contactos, sus mensajes y los participantes de
  # cada mensaje. stream() emite un chunk de nodos+edges por documento.
  def _pipeline(self, user_id: ObjectId) -> list:
    return [
      {"$match": {"user_id": user_id}},
      {"$lookup": {
        "from": "contacts",
        "localField": "contact_ids",
        "foreignField": "_id",
        "as": "contacts",
        # solo lo que se pinta: el cuerpo del contacto no viaja
        "pipeline": [{"$project": {"email": 1, "name": 1}}],
      }},
      {"$lookup": {
        "from": "messages",
        # el thread_id solo es único dentro de la cuenta: la pareja es la clave
        "let": {"integration_id": "$integration_id", "thread_id": "$thread_id"},
        "as": "messages",
        "pipeline": [
          # user_id primero: es el prefijo del índice de messages, sin él Mongo
          # no lo usa y escanea la colección entera por cada tarea
          {"$match": {"$expr": {"$and": [
            {"$eq": ["$user_id", user_id]},
            {"$eq": ["$integration_id", "$$integration_id"]},
            {"$eq": ["$thread_id", "$$thread_id"]},
          ]}}},
          {"$sort": {"internal_date": 1}},  # el orden real dentro del hilo
          # el cuerpo del correo no viaja: en el grafo solo se pinta la cabecera
          {"$project": {
            "sender": 1, "subject": 1, "internal_date": 1,
            "emails": {"$map": {
              "input": {"$regexFindAll": {
                "input": {"$concat": [
                  {"$ifNull": ["$sender", ""]}, " ",
                  {"$ifNull": ["$to", ""]}, " ",
                  {"$ifNull": ["$cc", ""]},
                ]},
                "regex": EMAIL,
              }},
              # los contactos se guardan siempre en minúsculas
              "as": "m", "in": {"$toLower": "$$m.match"},
            }},
          }},
          {"$lookup": {
            "from": "contacts",
            "localField": "emails",
            "foreignField": "email",
            "as": "participants",
            # el dueño del buzón y los que nunca llegaron a contacto no salen:
            # el $lookup solo casa con lo que existe en contacts
            "pipeline": [
              {"$match": {"$expr": {"$eq": ["$user_id", user_id]}}},
              {"$project": {"email": 1, "name": 1}},
            ],
          }},
        ],
      }},
      {"$project": {
        "_id": 0,
        "task": {
          "id": {"$toString": "$_id"},
          "type": "task",
          "label": "$title",
          "status": "$status",
          "priority": "$priority",
          "due_at": "$due_at",
          "integration_id": {"$toString": "$integration_id"},
          "thread_id": "$thread_id",
        },
        "contacts": {"$map": {"input": "$contacts", "as": "c", "in": {
          "id": {"$toString": "$$c._id"},
          "type": "contact",
          "label": {"$ifNull": ["$$c.name", "$$c.email"]},
          "email": "$$c.email",
        }}},
        "messages": {"$map": {"input": "$messages", "as": "m", "in": {
          "id": {"$toString": "$$m._id"},
          "type": "message",
          "label": "$$m.subject",
          "sender": "$$m.sender",
          "internal_date": "$$m.internal_date",
        }}},
        # quien iba en las cabeceras de cada correo sin ser de la tarea: puede no
        # estar en ninguna, así que también entra como nodo. Los que sí son de la
        # tarea se descartan: a su correo ya se llega por contacto -> tarea -> mensaje
        "participants": {"$map": {"input": "$messages", "as": "m", "in": {
          "message_id": {"$toString": "$$m._id"},
          "contacts": {"$map": {
            "input": {"$filter": {
              "input": "$$m.participants", "as": "p",
              "cond": {"$not": {"$in": ["$$p._id", "$contact_ids"]}},
            }},
            "as": "p", "in": {
              "id": {"$toString": "$$p._id"},
              "type": "contact",
              "label": {"$ifNull": ["$$p.name", "$$p.email"]},
              "email": "$$p.email",
            },
          }},
        }}},
      }},
    ]

  async def stream(self, user_id: ObjectId) -> AsyncIterator[bytes]:
    """Un chunk NDJSON por tarea, tal como las va devolviendo Mongo: el front
    puede empezar a pintar con la primera tarea en vez de esperar las 7000."""
    cursor = self.collection.aggregate(self._pipeline(user_id))
    async for doc in cursor:
      contacts = doc["contacts"] + [
        c for p in doc["participants"] for c in p["contacts"]
      ]
      edges = (
        [{"source": c["id"], "target": doc["task"]["id"]} for c in doc["contacts"]]
        + [{"source": doc["task"]["id"], "target": m["id"]} for m in doc["messages"]]
        + [
          {"source": c["id"], "target": p["message_id"]}
          for p in doc["participants"] for c in p["contacts"]
        ]
      )
      chunk = {
        "nodes": [doc["task"], *contacts, *doc["messages"]],
        "edges": edges,
      }
      yield (json.dumps(chunk, default=str) + "\n").encode()
