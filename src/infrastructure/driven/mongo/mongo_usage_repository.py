from dataclasses import asdict
from decimal import Decimal

from bson import ObjectId
from bson.decimal128 import Decimal128
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.domain.usage import Usage


def _to_document(usage: Usage) -> dict:
  document = asdict(usage)
  document["_id"] = document.pop("id")
  # Mongo no guarda Decimal: Decimal128 es el único tipo que no pierde céntimos
  cost = document.pop("cost")
  document["cost"] = Decimal128(cost) if cost is not None else None
  return document


def _to_usage(document: dict) -> Usage:
  document = dict(document)
  document["id"] = document.pop("_id")
  document.setdefault("email", None)  # filas anteriores a guardar el email
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
    """Lo que lleva gastado un usuario: la suma de todas sus llamadas."""
    cursor = self.collection.aggregate([
      {"$match": {"user_id": user_id}},
      {"$group": {
        "_id": None,
        "runs": {"$sum": 1},
        "input_tokens": {"$sum": "$input_tokens"},
        "output_tokens": {"$sum": "$output_tokens"},
        # $sum ignora los null, así que un modelo sin precio no rompe el total
        "cost": {"$sum": {"$toDecimal": {"$ifNull": ["$cost", 0]}}},
      }},
    ])
    totals = await cursor.to_list(1)
    if not totals:
      return {"runs": 0, "input_tokens": 0, "output_tokens": 0, "cost": Decimal(0)}

    total = totals[0]
    return {
      "runs": total["runs"],
      "input_tokens": total["input_tokens"],
      "output_tokens": total["output_tokens"],
      "cost": total["cost"].to_decimal(),
    }

  async def total_all_users(self) -> dict:
    """El gasto de la casa entera, sin desglosar por usuario."""
    cursor = self.collection.aggregate([
      {"$group": {
        "_id": None,
        "runs": {"$sum": 1},
        "input_tokens": {"$sum": "$input_tokens"},
        "output_tokens": {"$sum": "$output_tokens"},
        "cache_read_tokens": {"$sum": "$cache_read_tokens"},
        "reasoning_tokens": {"$sum": "$reasoning_tokens"},
        "cost": {"$sum": {"$toDecimal": {"$ifNull": ["$cost", 0]}}},
      }},
    ])
    totals = await cursor.to_list(1)
    if not totals:
      return {
        "runs": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "reasoning_tokens": 0,
        "cost": Decimal(0),
      }

    total = totals[0]
    return {
      "runs": total["runs"],
      "input_tokens": total["input_tokens"],
      "output_tokens": total["output_tokens"],
      "cache_read_tokens": total["cache_read_tokens"],
      "reasoning_tokens": total["reasoning_tokens"],
      "cost": total["cost"].to_decimal(),
    }

  async def totals_by_user(self) -> list[dict]:
    """Lo mismo pero para todos: una fila por usuario, el que más gasta primero.
    El email va como dato del grupo, no como clave: las filas viejas no lo tienen
    y agrupar por él las juntaría todas bajo null. Sale el último visto, que es
    el bueno aunque el usuario haya cambiado de correo o borrado la cuenta."""
    cursor = self.collection.aggregate([
      {"$sort": {"created_at": 1}},  # para que $last sea de verdad el último
      {"$group": {
        "_id": "$user_id",
        "email": {"$last": "$email"},
        "runs": {"$sum": 1},
        "input_tokens": {"$sum": "$input_tokens"},
        "output_tokens": {"$sum": "$output_tokens"},
        # van incluidos en input/output, se sacan aparte solo para ver el desglose
        "cache_read_tokens": {"$sum": "$cache_read_tokens"},
        "reasoning_tokens": {"$sum": "$reasoning_tokens"},
        "cost": {"$sum": {"$toDecimal": {"$ifNull": ["$cost", 0]}}},
      }},
      {"$sort": {"cost": -1}},
    ])
    return [
      {
        "user_id": total["_id"],
        "email": total.get("email"),
        "runs": total["runs"],
        "input_tokens": total["input_tokens"],
        "output_tokens": total["output_tokens"],
        "cache_read_tokens": total["cache_read_tokens"],
        "reasoning_tokens": total["reasoning_tokens"],
        "cost": total["cost"].to_decimal(),
      }
      async for total in cursor
    ]

  async def delete_all_by_user(self, user_id: ObjectId) -> int:
    result = await self.collection.delete_many({"user_id": user_id})
    return result.deleted_count
