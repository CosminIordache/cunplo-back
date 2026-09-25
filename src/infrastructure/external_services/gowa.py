import base64
import os
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

# GOWA (go-whatsapp-web-multidevice) corre como servicio aparte en Railway.
# Un device de GOWA = un número de WhatsApp = una Integration.
TIMEOUT = 20


class GowaError(Exception):
  """GOWA respondió con un error o no está configurado."""


def _check(response: httpx.Response) -> Any:
  if response.is_error:
    raise GowaError(f"GOWA {response.status_code}: {response.text[:200]}")
  return response.json().get("results") or {}


def _client() -> httpx.AsyncClient:
  # el entorno se lee al llamar, no al importar: el worker carga .env después de importar los jobs
  url = os.getenv("GOWA_URL", "").rstrip("/")
  if not url:
    raise GowaError("GOWA_URL is not set")
  user, _, password = os.getenv("GOWA_BASIC_AUTH", "").partition(":")
  return httpx.AsyncClient(base_url=url, auth=httpx.BasicAuth(user, password), timeout=TIMEOUT)


async def _request(method: str, path: str, **kwargs) -> Any:
  try:
    async with _client() as client:
      return _check(await client.request(method, path, **kwargs))
  except httpx.HTTPError as error:  # GOWA caído o sin red: mismo trato que un error suyo
    raise GowaError(f"GOWA unreachable: {error}") from error


async def add_device(device_id: str, webhook_url: str, secret: str) -> None:
  """Crea el hueco del device con su webhook. Todavía no hay número: se vincula con el
  QR o el código. GOWA firma cada aviso de este device con su secret."""
  await _request(
    "POST",
    "/devices",
    json={
      "device_id": device_id,
      "webhook_url": webhook_url,
      "webhook_secret": secret,
      "webhook_events": "message",  # el resto de eventos (acks, presencia...) no los usamos
    },
  )


async def login_qr(device_id: str) -> tuple[str, int]:
  """El QR como data URL y los segundos que vale. GOWA devuelve un enlace a su propio
  /statics, protegido con la misma Basic Auth: el navegador no lo puede pedir, así que
  lo descargamos aquí."""
  results = await _request("GET", f"/devices/{device_id}/login")
  # el host del enlace es el que ve GOWA (localhost detrás del proxy): solo vale la ruta
  path = urlsplit(results["qr_link"]).path
  try:
    async with _client() as client:
      image = await client.get(path)
  except httpx.HTTPError as error:
    raise GowaError(f"GOWA unreachable: {error}") from error
  if image.is_error:
    raise GowaError(f"GOWA QR {image.status_code}")
  encoded = base64.b64encode(image.content).decode()
  return f"data:image/png;base64,{encoded}", int(results.get("qr_duration") or 30)


async def login_code(device_id: str, phone: str) -> str:
  """El código de 8 caracteres que el usuario escribe en WhatsApp > Dispositivos vinculados.
  Sirve cuando Cunplo se usa en el mismo móvil y no puede escanear su propia pantalla."""
  # GOWA quiere el número sin '+'
  results = await _request("POST", f"/devices/{device_id}/login/code", params={"phone": phone.lstrip("+")})
  return results["pair_code"]


async def list_devices() -> list[dict]:
  return await _request("GET", "/devices") or []


async def device(device_id: str) -> dict:
  """{state: disconnected|connecting|connected|logged_in, jid, display_name, ...}"""
  return await _request("GET", f"/devices/{device_id}")


async def remove_device(device_id: str) -> None:
  """Cierra la sesión en WhatsApp y borra el device con sus datos en GOWA."""
  await _request("DELETE", f"/devices/{device_id}")


def jid_to_phone(jid: str) -> str | None:
  """'34600112233@s.whatsapp.net' (o con ':12' de dispositivo) -> '+34600112233'.
  Un JID '@lid' es un id anónimo, no un número: None."""
  user, _, server = (jid or "").partition("@")
  user = user.split(":")[0]
  return f"+{user}" if server == "s.whatsapp.net" and user.isdigit() else None


def _epoch_ms(timestamp: str) -> int:
  return int(datetime.fromisoformat(timestamp).timestamp() * 1000)


def to_message(payload: dict, owner_phone: str) -> dict:
  """El payload de un evento 'message' al mismo dict que gmail._to_message y
  outlook._to_message: aguas abajo solo cambia el canal."""
  sender = payload.get("from") or ""
  name = payload.get("sender_display_name") or payload.get("from_name") or ""
  address = jid_to_phone(sender) or sender
  return {
    "id": payload["id"],
    # el hilo es la conversación: un chat 1:1 es el JID de la otra persona
    "thread_id": payload["chat_id"],
    "sender": f"{name} <{address}>" if name else address,
    "to": owner_phone,
    "cc": "",
    "subject": "",
    "body": payload.get("body") or "",
    "internal_date": _epoch_ms(payload["timestamp"]),
  }
