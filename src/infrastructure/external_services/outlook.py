import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
API = f"{GRAPH}/me"
SUBSCRIPTIONS = f"{GRAPH}/subscriptions"
INBOX_DELTA = f"{API}/mailFolders/inbox/messages/delta"

# ponytail: solo los campos que guardamos. $select recorta la respuesta y, en delta,
# evita arrastrar el cuerpo de cada correo en cada página.
FIELDS = "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,body,isDraft,hasAttachments"


class OutlookError(Exception):
  """Graph respondió con un error."""


class DeltaTooOld(OutlookError):
  """El deltaLink caducó: toca resincronizar desde el estado actual."""


def _check(response: httpx.Response) -> httpx.Response:
  if response.is_error:
    raise OutlookError(f"Graph API {response.status_code}: {response.text[:200]}")
  return response


def _auth(access_token: str) -> dict:
  return {"Authorization": f"Bearer {access_token}"}


# Graph convierte el HTML a texto plano por nosotros, así body queda como el de Gmail.
# ponytail: sin parsear HTML aquí; si algún día hace falta el HTML original, quitar la cabecera.
_TEXT_BODY = {"Prefer": 'outlook.body-content-type="text"'}


def _address(recipient: dict | None) -> str:
  """'Nombre <email>' desde la estructura anidada de Graph."""
  address = (recipient or {}).get("emailAddress", {})
  name, email = address.get("name", ""), address.get("address", "")
  return f"{name} <{email}>" if name and name != email else email


def _to_message(raw: dict) -> dict:
  """Mismo dict que gmail._to_message: aguas abajo no distingue proveedor."""
  return {
    "id": raw["id"],
    "labels": ["DRAFT"] if raw.get("isDraft") else [],
    "thread_id": raw.get("conversationId"),
    "subject": raw.get("subject") or "",
    "sender": _address(raw.get("from")),
    "to": ", ".join(_address(r) for r in raw.get("toRecipients", [])),
    "cc": ", ".join(_address(r) for r in raw.get("ccRecipients", [])),
    # epoch ms como Gmail: es lo que ordena el hilo
    "internal_date": _epoch_ms(raw.get("receivedDateTime")),
    "body": (raw.get("body") or {}).get("content", ""),
    # el delta no trae los adjuntos, solo si los hay: se piden aparte con list_attachments
    "has_attachments": bool(raw.get("hasAttachments")),
    "attachments": [],
  }


def _epoch_ms(timestamp: str | None) -> int:
  from datetime import datetime

  if not timestamp:
    return 0
  return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)


async def _walk(http: httpx.AsyncClient, url: str, access_token: str, params: dict | None = None) -> tuple[list[dict], str]:
  """Recorre las páginas de un delta y devuelve los mensajes y el deltaLink final."""
  messages: list[dict] = []
  while True:
    response = await http.get(url, headers=_auth(access_token) | _TEXT_BODY, params=params)
    if response.status_code == 410:
      raise DeltaTooOld  # Graph invalida el token de delta pasado un tiempo
    data = _check(response).json()
    messages.extend(data.get("value", []))
    params = None  # los enlaces de paginación ya llevan la query dentro
    if next_link := data.get("@odata.nextLink"):
      url = next_link
      continue
    return messages, data["@odata.deltaLink"]


async def new_messages(access_token: str, delta_link: str) -> tuple[list[dict], str]:
  """Mensajes llegados desde delta_link, y el marcador nuevo."""
  async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
    raw, marker = await _walk(http, delta_link, access_token)
  # el delta también anuncia borrados y cambios de estado: solo queremos correos nuevos
  return [_to_message(m) for m in raw if "@removed" not in m], marker


async def list_attachments(access_token: str, message_id: str) -> list[dict]:
  """Adjuntos reales del correo, con el mismo shape que gmail._attachments.

  Graph devuelve contentBytes en el propio listado, así que no hace falta una
  segunda llamada por fichero. Se descartan las imágenes inline (logos de firma)
  y lo que no sea fileAttachment: un itemAttachment es un correo o cita anidada,
  no un fichero que se pueda subir tal cual."""
  async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
    response = await http.get(
      f"{API}/messages/{message_id}/attachments", headers=_auth(access_token)
    )
    data = _check(response).json()

  return [
    {
      "attachment_id": raw["id"],
      "filename": raw.get("name") or raw["id"],
      "mime_type": raw.get("contentType") or "application/octet-stream",
      "size": raw.get("size", 0),
      "content_bytes": raw.get("contentBytes"),  # base64, ya viene en la respuesta
    }
    for raw in data.get("value", [])
    if raw.get("@odata.type") == "#microsoft.graph.fileAttachment"
    and not raw.get("isInline")
  ]


async def get_attachment(access_token: str, message_id: str, attachment_id: str) -> bytes:
  """Los bytes de un adjunto suelto. Solo hace falta si no vinieron en list_attachments."""
  async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
    response = await http.get(
      f"{API}/messages/{message_id}/attachments/{attachment_id}/$value",
      headers=_auth(access_token),
    )
    return _check(response).content


async def subscribe(access_token: str, notification_url: str, secret: str, expires_at: str) -> dict:
  """Pide a Graph que avise de los correos nuevos. Caduca a los ~3 días."""
  async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
    response = await http.post(
      SUBSCRIPTIONS,
      headers=_auth(access_token),
      json={
        "changeType": "created",
        "notificationUrl": notification_url,
        "resource": "me/mailFolders('inbox')/messages",
        "expirationDateTime": expires_at,
        # vuelve en cada notificación: es lo que autentica el aviso
        "clientState": secret,
      },
    )
    return _check(response).json()  # {id, expirationDateTime, ...}


async def renew_subscription(access_token: str, subscription_id: str, expires_at: str) -> dict:
  """Alarga la caducidad. Más barato que crear una nueva."""
  async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
    response = await http.patch(
      f"{SUBSCRIPTIONS}/{subscription_id}",
      headers=_auth(access_token),
      json={"expirationDateTime": expires_at},
    )
    return _check(response).json()


async def unsubscribe(access_token: str, subscription_id: str) -> None:
  """Idempotente: borrar una que ya no existe da 404 y nos vale igual."""
  async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
    await http.delete(
      f"{SUBSCRIPTIONS}/{subscription_id}",
      headers=_auth(access_token),
    )


async def current_delta_link(access_token: str) -> str:
  """Marcador de 'a partir de ahora', sin traer el pasado."""
  async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
    # $top=1 y no 0: Graph rechaza el 0, pero solo nos interesa el deltaLink del final
    _, marker = await _walk(
      http, INBOX_DELTA, access_token, {"$select": FIELDS, "$top": "1"}
    )
  return marker
