from typing import Annotated
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException, status


def to_object_id(id: str) -> ObjectId:
  """ponytail: id validado en el borde, si no un id malformado revienta en 500."""
  try:
    return ObjectId(id)
  except InvalidId:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid id")


# Úsalo en cualquier ruta con un id de Mongo en el path: `entity_id: ObjectIdParam`
ObjectIdParam = Annotated[ObjectId, Depends(to_object_id)]
