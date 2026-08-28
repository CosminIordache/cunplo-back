from typing import TYPE_CHECKING, Annotated
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, status
from pydantic import AfterValidator


def to_object_id(id: str) -> ObjectId:
  """ponytail: id validado en el borde, si no un id malformado revienta en 500."""
  try:
    return ObjectId(id)
  except InvalidId:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid id")


# Úsalo en cualquier ruta con un id de Mongo en el path: `entity_id: ObjectIdParam`
# Es un validador, no un Depends: así funciona con cualquier nombre de path param.
if TYPE_CHECKING:
  # El type checker solo lee el primer arg de Annotated (str), pero en runtime
  # AfterValidator ya ha convertido el valor: le damos el tipo de salida real.
  ObjectIdParam = ObjectId
else:
  ObjectIdParam = Annotated[str, AfterValidator(to_object_id)]
