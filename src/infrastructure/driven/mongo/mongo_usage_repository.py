from dataclasses import asdict
from decimal import Decimal

from bson import ObjectId
from bson.decimal128 import Decimal128
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.domain.usage import Usage, UsageKind

# filas anteriores a guardar el kind: no se sabe si fueron task o assistant
_KIND = {"$ifNull": ["$kind", "unknown"]}

# primera pasada: sumas por kind. $sum ignora los null, así que un modelo sin
# precio no rompe el total
_SUMS = {
  "runs": {"$sum": 1},
  "input_tokens": {"$sum": "$input_tokens"},
  "output_tokens": {"$sum": "$output_tokens"},
  # van incluidos en input/output, se sacan aparte solo para ver el desglose
  "cache_read_tokens": {"$sum": "$cache_read_tokens"},
  "reasoning_tokens": {"$sum": "$reasoning_tokens"},
  "seconds": {"$sum": {"$ifNull": ["$seconds", 0]}},
  "cost": {"$sum": {"$toDecimal": {"$ifNull": ["$cost", 0]}}},
}
# segunda pasada: el total y la lista de kinds que lo componen
_ROLLUP = {
  **{field: {"$sum": f"${field}"} for field in _SUMS},
  "by_kind": {"$push": {
    "kind": "$_id.kind",
    "runs": "$runs",
    "input_tokens": "$input_tokens",
    "output_tokens": "$output_tokens",
    "seconds": "$seconds",
    "cost": "$cost",
  }},
}
_EMPTY = {field: 0 for field in _SUMS} | {"cost": Decimal(0), "by_kind": {}}


def _rollup(total: dict) -> dict:
  return {
    **{field: total[field] for field in _SUMS},
    "cost": total["cost"].to_decimal(),
    "by_kind": {
      line.pop("kind"): line | {"cost": line["cost"].to_decimal()} for line in total["by_kind"]
    },
  }


def _to_document(usage: Usage) -> dict:
  document = asdict(usage)
  document["_id"] = document.pop("id")
  document["kind"] = usage.kind.value
  # Mongo no guarda Decimal: Decimal128 es el único tipo que no pierde céntimos
  cost = document.pop("cost")
  document["cost"] = Decimal128(cost) if cost is not None else None
  return document


def _to_usage(document: dict) -> Usage:
  document = dict(document)
  document["id"] = document.pop("_id")
  document.setdefault("email", None)  # filas anteriores a guardar el email
  document.setdefault("seconds", 0.0)
  # ponytail: las filas sin kind se leen como task, en los totales salen como "unknown"
  document["kind"] = UsageKind(document.get("kind", UsageKind.TASK))
  cost = document.pop("cost")
  document["cost"] = cost.to_decimal() if cost is not None else None
  return Usage(**document)


class MongoUsageRepository:
  def __init__(self, db: AsyncIOMotorDatabase):
    self.collection = db["usages"]

  async def create(self, usage: Usage) -> Usage:
    await self.collection.insert_one(_to_document(usage))
    return usage

  async def total_by_user(self, user_id: ObjectId) -> dict:
    """Lo que lleva gastado un usuario: la suma de todas sus llamadas, por kind."""
    cursor = self.collection.aggregate([
      {"$match": {"user_id": user_id}},
      {"$group": {"_id": {"kind": _KIND}, **_SUMS}},
      {"$group": {"_id": None, **_ROLLUP}},
    ])
    totals = await cursor.to_list(1)
    return _rollup(totals[0]) if totals else dict(_EMPTY)

  async def total_all_users(self) -> dict:
    """El gasto de la casa entera, sin desglosar por usuario pero sí por kind."""
    cursor = self.collection.aggregate([
      {"$group": {"_id": {"kind": _KIND}, **_SUMS}},
      {"$group": {"_id": None, **_ROLLUP}},
    ])
    totals = await cursor.to_list(1)
    return _rollup(totals[0]) if totals else dict(_EMPTY)

  async def totals_by_user(self) -> list[dict]:
    """Lo mismo pero para todos: una fila por usuario, el que más gasta primero.
    El email va como dato del grupo, no como clave: las filas viejas no lo tienen
    y agrupar por él las juntaría todas bajo null. Sale el último visto, que es
    el bueno aunque el usuario haya cambiado de correo o borrado la cuenta."""
    cursor = self.collection.aggregate([
      {"$sort": {"created_at": 1}},  # para que $last sea de verdad el último
      {"$group": {
        "_id": {"user_id": "$user_id", "kind": _KIND},
        "email": {"$last": "$email"},
        "last_at": {"$max": "$created_at"},
        **_SUMS,
      }},
      {"$sort": {"last_at": 1}},  # el $group anterior pierde el orden
      {"$group": {"_id": "$_id.user_id", "email": {"$last": "$email"}, **_ROLLUP}},
      {"$sort": {"cost": -1}},
    ])
    return [
      {"user_id": total["_id"], "email": total.get("email"), **_rollup(total)}
      async for total in cursor
    ]

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    result = await self.collection.delete_many({"user_id": user_id})
    return result.deleted_count
