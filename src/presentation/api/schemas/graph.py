from typing import List, Literal, Optional
from datetime import datetime
from pydantic import BaseModel

from src.domain.task import Priority, Status


class GraphNode(BaseModel):
  id: str
  type: Literal["task", "contact", "message"]
  label: str  # título de la tarea, nombre del contacto o asunto del correo

  # de tarea
  status: Optional[Status] = None
  priority: Optional[Priority] = None
  due_at: Optional[datetime] = None
  integration_id: Optional[str] = None
  thread_id: Optional[str] = None

  # de contacto
  email: Optional[str] = None

  # de mensaje
  sender: Optional[str] = None
  internal_date: Optional[int] = None  # epoch ms: el orden dentro del hilo


class GraphEdge(BaseModel):
  """Siempre hacia abajo: contacto -> tarea -> mensaje."""

  source: str
  target: str


class GraphOut(BaseModel):
  nodes: List[GraphNode]
  edges: List[GraphEdge]
