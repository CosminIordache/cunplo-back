import base64
from typing import Optional
import logfire
import httpx

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class HistoryTooOld(Exception):
  """El historyId guardado caducó (Gmail guarda ~1 semana): toca resincronizar."""


class GmailError(Exception):
  """Gmail respondió con un error. Envuelve el HTTPStatusError de httpx."""


class MessageNotFound(GmailError):
  """El correo ya no está: borrado o movido entre que el historial lo anunció y lo pedimos."""


def _check(response: httpx.Response) -> httpx.Response:
  """Traduce el error crudo de httpx a uno nuestro, con el detalle de Gmail."""
  if response.is_error:
    raise GmailError(f"Gmail API {response.status_code}: {response.text[:200]}")
  return response


def _header(payload: dict, name: str) -> str:
  return next(
    (h["value"] for h in payload.get("headers", []) if h["name"].lower() == name), ""
  )


def _body(payload: dict) -> str:
  """Texto plano del mensaje, bajando por las partes MIME."""
  if payload.get("mimeType") == "text/plain":
    if data := payload.get("body", {}).get("data"):
      return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
  for part in payload.get("parts", []):
    if text := _body(part):
      return text
  return ""


def _to_message(raw: dict) -> dict:
  payload = raw.get("payload", {})
  return {
    "id": raw["id"],
    "labels": raw.get("labelIds", []),
    "thread_id": raw.get("threadId"),
    "subject": _header(payload, "subject"),
    "sender": _header(payload, "from"),
    "to": _header(payload, "to"),
    "cc": _header(payload, "cc"),
    # el 'Date' de la cabecera lo pone el remitente; este lo pone Gmail
    "internal_date": int(raw["internalDate"]),
    "body": _body(payload),
  }


def _auth(access_token: str) -> dict:
  return {"Authorization": f"Bearer {access_token}"}


async def get_message(access_token: str, message_id: str) -> dict:
  async with httpx.AsyncClient(timeout=30) as http:
    response = await http.get(f"{API}/messages/{message_id}", headers=_auth(access_token))
    if response.status_code == 404:
      raise MessageNotFound(message_id)
    raw = _check(response).json()
    return _to_message(raw)


async def new_message_ids(access_token: str, start_history_id: str) -> tuple[list[str], str]:
  """IDs de mensajes añadidos desde start_history_id, y el nuevo marcador."""
  async with httpx.AsyncClient(timeout=30) as http:
    response = await http.get(
      f"{API}/history",
      headers=_auth(access_token),
      params={
        "startHistoryId": start_history_id,
        "historyTypes": "messageAdded",
        "labelId": "INBOX",
      },
    )
    if response.status_code == 404:
      raise HistoryTooOld
    data = _check(response).json()

  ids = [
    added["message"]["id"]
    for entry in data.get("history", [])
    for added in entry.get("messagesAdded", [])
  ]
  # sin cambios Gmail omite historyId: conserva el marcador que ya teníamos
  return ids, str(data.get("historyId") or start_history_id)


async def current_history_id(access_token: str) -> str:
  """Marcador actual del buzón, para empezar a seguir desde aquí."""
  async with httpx.AsyncClient(timeout=30) as http:
    response = await http.get(f"{API}/profile", headers=_auth(access_token))
    return str(_check(response).json()["historyId"])


async def watch(access_token: str, topic: str) -> dict:
  """Pide a Gmail que publique los cambios en el topic de Pub/Sub. Caduca en 7 días."""
  async with httpx.AsyncClient(timeout=30) as http:
    response = await http.post(
      f"{API}/watch",
      headers=_auth(access_token),
      json={"topicName": topic, "labelIds": ["INBOX"]},
    )
    return _check(response).json()  # {historyId, expiration}


async def stop_watch(access_token: str) -> None:
  async with httpx.AsyncClient(timeout=30) as http:
    await http.post(f"{API}/stop", headers=_auth(access_token))
