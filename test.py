import asyncio
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import StrEnum
from typing import Optional
from pydantic_ai import Agent
from dotenv import load_dotenv
load_dotenv()


class TasksStatus(StrEnum):
  UNCLASIFIED = "unclasified"
  USER = "user"
  CUSTOMER = "customer"

@dataclass
class Task:
  name: str
  customer_name: str
  status: TasksStatus
  due_date: Optional[datetime] = None

agent = Agent(
  'openai:gpt-5.4-nano',
  output_type=list[Task],
  instructions="""Extrae tareas de un correo. Responde en español.

- Si el correo no pide ninguna acción (newsletter, spam, confirmación, gracias), devuelve lista vacía.
- status USER: la acción la tengo que hacer yo (el destinatario).
- status CUSTOMER: la acción la tiene que hacer el cliente que escribe.
- status UNCLASIFIED: si dudas de quién es la tarea. Ante la duda, UNCLASIFIED.
- customer_name: quien envía el correo.
- due_date: solo si el correo da una fecha concreta, si no null.""",
)

# ponytail: casos de prueba a mano, mueve a test_*.py cuando haya assertions reales
EMAILS = [
  "De: Laura (Acme). Hola, ¿me puedes enviar la factura de marzo antes del 2026-03-15? Gracias.",
  "De: Newsletter Python Weekly. Las 10 novedades de Python 3.14 que te vas a perder.",
  "De: Pedro (Beta SL). Te paso el logo revisado. Yo me encargo de subirlo al servidor esta semana.",
  "De: Marta (Gamma). Habría que revisar el contrato antes del viernes, alguien tendría que mirarlo.",
  "De: Juan (Delta). Recibido, muchas gracias por todo!",
]


async def main():
  for email in EMAILS:
    result = await agent.run(email)
    print(f'\n--- {email[:50]}...')
    for task in result.output:
      print(f'  [{task.status}] {task.name} ({task.customer_name}) due={task.due_date}')
    if not result.output:
      print('  (sin tareas)')


asyncio.run(main())